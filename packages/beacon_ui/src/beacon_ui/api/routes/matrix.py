"""The results matrix, and the questions a benchmark scores against.

The matrix is the page the hand-maintained benchmark tracker becomes: one row
per system · version · model · config, aggregated over that configuration's
valid runs. Nothing here re-grades — it reads the outcomes ingestion already
composed, with the register's semantics: ERROR leaves the denominator, DEFER
stays in it, and a rate over nothing gradeable is None.
"""

from __future__ import annotations

import hashlib
from datetime import datetime  # noqa: TC003
from typing import Annotated
from uuid import UUID  # noqa: TC003

import sqlalchemy as sa
from beacon_iam.permissions import Permission
from beacon_storage.models.eval_items import EvalItem
from beacon_storage.models.regrade_events import RegradeEvent
from beacon_storage.models.runs import Result, Run, Verdict
from beacon_storage.models.solutions import Solution
from beacon_storage.models.tenancy import User  # noqa: TC002
from beacon_storage.repository.regrade_events import RegradeEventRepo
from beacon_storage.repository.suites import SuiteRepo
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session  # noqa: TC002

from beacon_ui.api.deps import get_session, require_permission
from beacon_ui.api.openapi import requires
from beacon_ui.api.schemas.matrix import (
    MatrixAsOfOut,
    MatrixCurrentOut,
    MatrixOut,
    MatrixRowOut,
    SuiteItemListOut,
    SuiteItemRowOut,
)

router = APIRouter(prefix="/v1", tags=["matrix"])

_GRADED = ("PASS", "FAIL", "DEFER")


def _item_join_clause() -> sa.ColumnElement[bool]:
    """Join a result to the live version of the item it answers."""
    return sa.and_(
        sa.cast(Result.item_id, sa.Uuid) == EvalItem.item_id,
        EvalItem.valid_to.is_(None),
    )


def _row_key(
    solution_id: object,
    model_id: object,
    config_label: object,
    config_digest: object,
    engine: object,
    retrieval_k: object,
) -> str:
    """A stable handle for one matrix row, derived from its own group key.

    Exists so a row can be named in a URL. ``config_digest`` cannot do that
    job: it is a hash of the run CONFIG, and the model is not in the config --
    measured, one spider2 digest is shared by four rows differing only by
    ``model_id``, so a cell page restored from a digest would open a different
    configuration than the one clicked. That is the failure this key exists to
    prevent, not a hypothetical.

    Derived from six of the eight expressions the statement GROUPs BY. The two
    omitted -- ``solution_name`` and ``solution_version`` -- are functionally
    determined by ``solution_id``, which IS here, so no two groups can share a
    key through them. That dependency is the whole of the "by construction"
    argument and is stated because the argument is worthless without it.

    NULL and the empty string are kept DISTINCT, using a sentinel rather than
    ``str(x or "")``. Both ``model_id`` and ``config_label`` are nullable, so
    the GROUP BY treats NULL and ``''`` as different groups; folding them
    together here would give those two rows one key and open the wrong one.
    NUL cannot appear in a Postgres text value, so it is unambiguous as the
    marker.

    Fields are LENGTH-PREFIXED, not merely separated. An earlier version joined
    on US (0x1f) and claimed no value could contain it -- which is not a
    property of the schema: ``model_id`` and ``config_label`` are plain
    ``String(200)`` fed from the run-create payload, and 0x1f is a legal text
    byte. Measured: a label carrying one collided two rows the GROUP BY keeps
    apart, so the cell URL opened the other configuration -- the exact failure
    this key exists to prevent. Named narrowly on purpose: those two are the
    free-text ones. ``config_digest`` is hex, and ``engine`` and
    ``retrieval_k`` are JSONB extractions rather than columns at all, so a
    wider claim here would not hold for the set it covered.

    A length prefix makes the encoding injective whatever the bytes are,
    because the length says where each field ends
    rather than a byte that might not be reserved. Opaque, because it is a
    handle rather than data -- nothing should parse it.

    Truncated to 32 hex characters, which is 128 bits, and NOT to the 16 it
    once was. The old argument was about accident: 64 bits is far past
    collision range for a table of tens of rows, which is true and answers the
    wrong question. FIVE of the six fields come from the run-create payload,
    not two as this note first said: ``model_id``, ``config_label`` and
    ``config_digest`` are each derived from the posted ``config``
    (``model_id_of``, ``config_label_of``, ``config_digest``), and ``engine``
    and ``retrieval_k`` are read straight back out of it. Only ``solution_id``
    is not chosen in the payload.

    Be exact about what that reaches, because this note has now been wrong
    about it twice in opposite directions. At a 64-bit key width, a birthday
    search costs about 2**32 evaluations and finds SOME colliding pair among
    inputs the searcher chose -- so both halves are rows they created
    themselves, and the reachable outcome is a link naming one of their own
    configurations that opens another. Colliding with a row somebody ELSE owns
    is a second preimage, which 64 bits already put at 2**64. This width is
    therefore hardening against a self-collision, not the closing of an open
    door.

    Nothing is claimed here about what the breadth of caller control buys on
    top of that. Two attempts at it were wrong -- the search cost follows from
    the key width alone, and the "the halves can look ordinary" version did not
    survive the 2**32 count -- and the argument for the width does not need
    either of them.

    Widened anyway, on cost rather than on severity: the client resolves a
    handle by FIRST MATCH, the fix is one character, and the handles were
    eleven hours old when it was made -- 1795eb6 to b1ecf0b, an interval rather
    than a date, because the two straddle midnight UTC and naming a day would
    read as wrong to anyone checking in a different zone. A width change
    invalidates every handle already emitted, so the cheap moment to pick a
    width is the only one there is.
    """
    # `\x00` for absent, the value itself otherwise -- NOT `x or ""`, which
    # maps NULL and '' to one key while the GROUP BY keeps them apart.
    parts = [
        "\x00" if value is None else str(value)
        for value in (
            solution_id,
            model_id,
            config_label,
            config_digest,
            engine,
            retrieval_k,
        )
    ]
    encoded = "".join(f"{len(part)}:{part}" for part in parts)
    return hashlib.sha256(encoded.encode()).hexdigest()[:32]


def _rate(part: int, whole: int) -> float | None:
    """A rate over nothing gradeable is unknown, not zero."""
    return part / whole if whole else None


def _per_run_rate(
    filters: list[sa.ColumnElement[bool]], *, reversal: sa.Subquery | None = None
) -> sa.Subquery:
    """Each valid run's own headline rate, one row per run.

    Rewound with the row when an as-of is in force. It has to be: this feeds
    the min/max spread beside the rate, and a current spread bracketing a
    rewound mean is a row that contradicts itself -- 47.2% with a range of
    50.9-50.9.

    A row aggregating several runs reports one number, and a reader takes it
    for a quantity. It is a mean over repetitions that disagree -- measured on
    the first real sweep, one arm's three passes read 53.4 / 58.6 / 51.7 and
    the row said 54.3. Repeating a config is how you learn the error bar, so
    the row must be able to SHOW the spread rather than average it away.
    """
    outcome = (
        Result.outcome
        if reversal is None
        else sa.func.coalesce(reversal.c.before_outcome, Result.outcome)
    )
    stmt = (
        sa.select(
            Run.id.label("run_id"),
            (
                sa.cast(sa.func.count().filter(outcome == "PASS"), sa.Float)
                / sa.func.nullif(sa.func.count().filter(outcome.in_(_GRADED)), 0)
            ).label("rate"),
        )
        .select_from(Run)
        .join(Result, Result.run_id == Run.id)
    )
    if reversal is not None:
        stmt = stmt.join(reversal, reversal.c.result_id == Result.id, isouter=True)
    return stmt.where(*filters).group_by(Run.id).subquery("per_run_rate")


def _latest_reading(metric: str, *, before: datetime | None = None) -> sa.Subquery:
    """The verdict per result for one metric, one row each -- current, or as-of.

    ``before`` makes it as-of: the newest verdict that EXISTED at that moment.
    This is the cheap half of the rewind, and the reason it is cheap is that
    verdicts are append-only and versioned -- nothing was ever overwritten, so
    reading them at a past instant is a plain filter. ``Result.outcome`` is the
    expensive half, because it is one column written in place.

    The cutoff is strict (``<``). A regrade writes its verdicts and its event
    in ONE transaction, so both carry the same ``now()``: ``<`` excludes
    exactly the verdicts that regrade produced, which is what "before this
    event" has to mean. ``<=`` would include them and the readings would
    disagree with the outcomes beside them.

    Grader versions accumulate on a result (history, not garbage), so a bare
    join sees every era -- and an aggregate over it silently means "any
    version true". One row per result: the most recently written reading.
    Newest by id, not by version string -- ids are UUIDv7 (write-ordered),
    while "v10" sorts before "v2" as text.
    """
    verdict = sa.orm.aliased(Verdict)
    return (
        sa.select(
            verdict.result_id,
            verdict.bool_value,
            # The reading's own declared scope. Only got_facts sets it, and
            # only from the version that began declaring it -- NULL means "this
            # verdict predates the disclosure", which is not the same as "the
            # whole table was compared" and must not be counted as it.
            verdict.raw_output["scope"].astext.label("scope"),
        )
        .where(
            verdict.metric == metric,
            *([verdict.created_at < before] if before is not None else []),
        )
        .distinct(verdict.result_id)
        .order_by(verdict.result_id, verdict.id.desc())
        .subquery(f"latest_{metric}" + ("_then" if before is not None else ""))
    )


def _outcome_reversal(suite_id: UUID, event: RegradeEvent) -> sa.Subquery:
    """Per result, the outcome it held BEFORE the selected event.

    Built in SQL from the events' own ``outcome_changes`` rather than from a
    Python-side dict, so a suite with a long history does not have to be paged
    into memory to answer one request.

    Every event at or after the selected one contributes, and the OLDEST
    ``before`` wins -- ``DISTINCT ON`` with the events ordered ascending. That
    is the whole subtlety: if a result moved in event E and again in E+1,
    "before E" is E's before-value, not E+1's. Taking the newest would report
    the intermediate state as though it were the original.

    Ordered by ``(created_at, id)`` because ``created_at`` is transaction-
    scoped: two events written in one transaction share it exactly, and the
    uuid7 id is what breaks the tie in write order.

    Results no recorded event touched are absent here, and the caller falls
    back to the current outcome for them. That is a real assumption and it is
    the one the UI has to disclose: it holds for grading changes, which are
    what events record, and NOT for an outcome that moved by some other path
    before recording began.
    """
    # The column has to be declared JSONB or the `["result_id"]` subscript is
    # not available on it -- a bare table_valued("value") comes back untyped.
    changes = sa.func.jsonb_array_elements(RegradeEvent.outcome_changes).table_valued(
        sa.column("value", JSONB)
    )
    result_id = sa.cast(changes.c.value["result_id"].astext, sa.Uuid)
    return (
        sa.select(
            result_id.label("result_id"),
            changes.c.value["before"].astext.label("before_outcome"),
            # WHICH event's change won the DISTINCT ON. Because the ordering is
            # ascending and the selected event is the earliest row this filter
            # admits, this equals the selected event's id exactly when the
            # SELECTED event touched the result -- and names a later event when
            # only a later one did. Without it the caller can tell that
            # something moved since the selected point but not whether the
            # selected event had anything to do with it, which is the
            # distinction `affected` claims to draw.
            RegradeEvent.id.label("event_id"),
        )
        .select_from(RegradeEvent)
        .join(changes, sa.true())
        .where(
            RegradeEvent.suite_id == suite_id,
            sa.tuple_(RegradeEvent.created_at, RegradeEvent.id)
            >= sa.tuple_(sa.literal(event.created_at), sa.literal(event.id)),
        )
        .distinct(result_id)
        .order_by(result_id, RegradeEvent.created_at.asc(), RegradeEvent.id.asc())
        .subquery("outcome_reversal")
    )


@router.get(
    "/suites/{suite_id}/results-matrix",
    response_model=MatrixOut,
    summary="Aggregate results, one row per system · version · model · config",
)
@requires(Permission.EVAL_VIEW)
def results_matrix(
    suite_id: UUID,
    _actor: Annotated[
        User,
        Depends(require_permission(Permission.EVAL_VIEW, scope_kind="suite")),
    ],
    session: Annotated[Session, Depends(get_session)],
    difficulty: Annotated[str | None, Query()] = None,
    as_of_event: Annotated[
        UUID | None,
        Query(description="Read every rate as it stood BEFORE this recorded regrade."),
    ] = None,
) -> MatrixOut:
    """Group valid runs by configuration identity and aggregate their results.

    ``as_of_event`` rewinds the grading. What it does NOT rewind is the run
    set: a rate that moved because a run landed or was invalidated is
    untouched, and that is not hypothetical -- the matrix pools every valid run
    with no time bound. The response says so in ``as_of.rewinds``, because a
    selector that silently rewound one of the two reasons a number moves would
    be worse than none.
    """
    filters: list[sa.ColumnElement[bool]] = [
        Run.suite_id == suite_id,
        Run.invalidated_at.is_(None),
    ]
    event: RegradeEvent | None = None
    if as_of_event is not None:
        event = RegradeEventRepo(session).get_for_suite(suite_id=suite_id, event_id=as_of_event)
        if event is None:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, "no such recorded regrade for this benchmark"
            )
    engine_expr = sa.func.coalesce(Run.config["engine"].astext, "")
    retrieval_k_expr = Run.config["retrieval_k"].astext
    # Two verdict readings: the strict exact_match beside the tolerant
    # got_facts. EX stays the benchmark-headline number (each benchmark
    # defines its own); these two are beacon's suite-independent claims, one
    # grader across benchmarks -- and each read at its CURRENT version only.
    got_facts_now = _latest_reading("got_facts")
    exact_now = _latest_reading("exact_match")

    # As-of, the two column families are rewound by DIFFERENT mechanisms,
    # because they were stored differently. The readings are append-only, so
    # "as it stood" is a filter on when the verdict was written. The outcome is
    # one column overwritten in place, so it has to be reconstructed backwards
    # from what the events recorded. Same instant, two routes to it.
    reversal = None if event is None else _outcome_reversal(suite_id, event)
    # Bound here, beside the reversal it belongs to, so the aggregate can name
    # the selected event without the type checker having to prove that
    # `reversal is not None` implies `event is not None`.
    selected_event_id = None if event is None else event.id
    if event is not None:
        got_facts_read = _latest_reading("got_facts", before=event.created_at)
        exact_read = _latest_reading("exact_match", before=event.created_at)
    else:
        got_facts_read, exact_read = got_facts_now, exact_now

    # Every rate below is computed over THIS expression, not `Result.outcome`
    # directly. With no as-of it IS `Result.outcome`; with one it is the
    # outcome the result held before the selected event, falling back to the
    # current value for results no recorded event touched.
    outcome = (
        Result.outcome
        if reversal is None
        else sa.func.coalesce(reversal.c.before_outcome, Result.outcome)
    )
    per_run = _per_run_rate(filters, reversal=reversal)

    stmt = (
        sa.select(
            Run.solution_id,
            Solution.solution_id.label("solution_name"),
            Solution.version.label("solution_version"),
            Run.model_id,
            Run.config_label,
            Run.config_digest,
            # A knob that is in the digest but on no screen splits a row and
            # cannot say why. Two retrieval depths under the default label
            # ("single-shot") are two rows, correctly, and identical to look at
            # -- which is how a reader attributes a difference to the wrong
            # thing. Named explicitly rather than dumping the config: that blob
            # can carry a secret reference, and a table is not the place to
            # discover one.
            retrieval_k_expr.label("retrieval_k"),
            engine_expr.label("engine"),
            # The arms pooled into this row, made visible: a sweep run's
            # config_label is empty and its identity lives in sweep_arm, so a
            # blank cell over a pooled average answered nobody's question.
            # More than one distinct arm in the cell is itself information --
            # the row is pooling things a reader may not want pooled.
            sa.func.string_agg(Run.sweep_arm.distinct(), sa.literal(", ")).label("arms"),
            sa.func.count(sa.func.distinct(Run.id)).label("n_runs"),
            # The spread across the runs pooled here. min/max survive the
            # row multiplication this join causes (unlike a sum), so the
            # per-run subquery can ride along with the item-level counts.
            sa.func.min(per_run.c.rate).label("ex_rate_min"),
            sa.func.max(per_run.c.rate).label("ex_rate_max"),
            sa.func.count(sa.func.distinct(Result.id))
            .filter(outcome.in_(_GRADED))
            .label("n_graded"),
            sa.func.count(sa.func.distinct(Result.id)).filter(outcome == "PASS").label("n_pass"),
            sa.func.count(sa.func.distinct(Result.id)).filter(outcome == "FAIL").label("n_fail"),
            sa.func.count(sa.func.distinct(Result.id)).filter(outcome == "DEFER").label("n_defer"),
            sa.func.count(sa.func.distinct(Result.id)).filter(outcome == "ERROR").label("n_errors"),
            # BIRD-comparable numerator: a pass whose SQL the runner verified
            # against the gold's engine. Only meaningful when the runner
            # supplied the flag at all -- see n_portability_flagged.
            sa.func.count(sa.func.distinct(Result.id))
            .filter(
                outcome == "PASS",
                sa.func.coalesce(Result.output["portable_to_gold_engine"].astext, "true")
                != "false",
            )
            .label("n_pass_target_engine"),
            sa.func.count(sa.func.distinct(Result.id))
            .filter(Result.output["portable_to_gold_engine"].astext.isnot(None))
            .label("n_portability_flagged"),
            # The tolerant reading of the same execution. The subquery joins
            # are one row per result by construction; DISTINCT stays anyway --
            # the guarantee belongs in the query, not in a comment about
            # today's shape.
            #
            # _GRADED on the numerators too, and for the same reason the
            # denominator has it. A verdict outlives an ERROR composite, so
            # without this a true reading on an excluded result counts into a
            # rate that excluded the result -- 3 over 2 on a PASS/PASS/ERROR
            # row, which the UI renders as "150%".
            sa.func.count(sa.func.distinct(Result.id))
            .filter(outcome.in_(_GRADED), got_facts_read.c.bool_value.is_(True))
            .label("n_got_facts"),
            sa.func.count(sa.func.distinct(Result.id))
            .filter(outcome.in_(_GRADED), exact_read.c.bool_value.is_(True))
            .label("n_exact"),
            # How many results the metric was READ on, true or false. The
            # numerator cannot answer that: a system that matched nothing and
            # a metric that never ran both count zero, and only one of them is
            # a measurement. These decide None vs 0.0 below.
            #
            # Restricted to _GRADED, matching the denominator they gate and
            # the numerators above. Without it, a row whose graded results
            # were never scored reports 0.0 on the strength of a verdict
            # attached to a result the rate excludes.
            sa.func.count(sa.func.distinct(Result.id))
            .filter(outcome.in_(_GRADED), got_facts_read.c.bool_value.isnot(None))
            .label("n_got_facts_scored"),
            # The breakdown of that total, three ways and each counted
            # DIRECTLY. `condition_cols` restricts the tolerant reading to the
            # benchmark's scored columns, so on 45 of spider2's 135 items this
            # metric asks about a strict subset of gold -- 20 of them a single
            # column -- and one number spanning both questions cannot be
            # compared across rows (B72). None of these is derived by
            # subtracting the others: that is how a numerator came to count
            # what its denominator had thrown out (B68).
            sa.func.count(sa.func.distinct(Result.id))
            .filter(
                outcome.in_(_GRADED),
                got_facts_read.c.bool_value.isnot(None),
                got_facts_read.c.scope == "subset",
            )
            .label("n_got_facts_subset_scored"),
            sa.func.count(sa.func.distinct(Result.id))
            .filter(
                outcome.in_(_GRADED),
                got_facts_read.c.bool_value.isnot(None),
                got_facts_read.c.scope == "full",
            )
            .label("n_got_facts_full_scored"),
            sa.func.count(sa.func.distinct(Result.id))
            .filter(
                outcome.in_(_GRADED),
                got_facts_read.c.bool_value.isnot(None),
                got_facts_read.c.scope.is_(None),
            )
            .label("n_got_facts_scope_unknown"),
            sa.func.count(sa.func.distinct(Result.id))
            .filter(outcome.in_(_GRADED), exact_read.c.bool_value.isnot(None))
            .label("n_exact_scored"),
            sa.func.percentile_cont(0.5)
            .within_group(Result.tokens_input + Result.tokens_output)
            .label("median_tokens"),
            sa.func.percentile_cont(0.5).within_group(Result.runtime_ms).label("median_runtime"),
            # The CURRENT values, carried alongside so the caller can render a
            # delta and mark the cells that moved. Only the columns that can
            # move: a single delta on the headline would leave the other three
            # to change in silence, which is the failure this whole feature
            # exists to end. `deferred` is here for completeness of the
            # outcome triple even though a regrade cannot move it -- it
            # re-derives only results already PASS or FAIL.
            *(
                []
                if reversal is None
                else [
                    sa.func.count(sa.func.distinct(Result.id))
                    .filter(Result.outcome.in_(_GRADED))
                    .label("n_graded_now"),
                    sa.func.count(sa.func.distinct(Result.id))
                    .filter(Result.outcome == "PASS")
                    .label("n_pass_now"),
                    sa.func.count(sa.func.distinct(Result.id))
                    .filter(Result.outcome == "FAIL")
                    .label("n_fail_now"),
                    sa.func.count(sa.func.distinct(Result.id))
                    .filter(Result.outcome == "DEFER")
                    .label("n_defer_now"),
                    sa.func.count(sa.func.distinct(Result.id))
                    .filter(Result.outcome.in_(_GRADED), exact_now.c.bool_value.is_(True))
                    .label("n_exact_now"),
                    sa.func.count(sa.func.distinct(Result.id))
                    .filter(Result.outcome.in_(_GRADED), exact_now.c.bool_value.isnot(None))
                    .label("n_exact_scored_now"),
                    sa.func.count(sa.func.distinct(Result.id))
                    .filter(Result.outcome.in_(_GRADED), got_facts_now.c.bool_value.is_(True))
                    .label("n_got_facts_now"),
                    sa.func.count(sa.func.distinct(Result.id))
                    .filter(
                        Result.outcome.in_(_GRADED),
                        got_facts_now.c.bool_value.isnot(None),
                    )
                    .label("n_got_facts_scored_now"),
                    # TWO counts, because they answer two different questions
                    # and one number was being used for both.
                    #
                    # `n_changed_since` is every result this row rewound at
                    # all, so it says whether there is a delta to show. The
                    # reversal admits the selected event AND every later one,
                    # which is correct for reconstructing the past -- the
                    # rewound number has to account for everything that
                    # happened since.
                    #
                    # `n_touched_by_event` is the narrower claim, the one the
                    # `affected` flag and its tooltip actually make: that THIS
                    # event moved something here. Selecting an older event on a
                    # suite with a later one used to report every row a later
                    # regrade touched as this event's doing.
                    #
                    # Both read from the reversal join rather than inferring
                    # from the rates being equal: a row can have two changes
                    # that cancel, and "the number happens to match" is not the
                    # same statement as "this event did not touch it".
                    sa.func.count(sa.func.distinct(Result.id))
                    .filter(reversal.c.result_id.isnot(None))
                    .label("n_changed_since"),
                    sa.func.count(sa.func.distinct(Result.id))
                    .filter(reversal.c.event_id == sa.literal(selected_event_id))
                    .label("n_touched_by_event"),
                ]
            ),
        )
        .join(Result, Result.run_id == Run.id)
        .join(Solution, Solution.id == Run.solution_id)
        .join(got_facts_read, got_facts_read.c.result_id == Result.id, isouter=True)
        .join(exact_read, exact_read.c.result_id == Result.id, isouter=True)
        .join(per_run, per_run.c.run_id == Run.id, isouter=True)
        .where(*filters)
        .group_by(
            Run.solution_id,
            Solution.solution_id,
            Solution.version,
            Run.model_id,
            Run.config_label,
            Run.config_digest,
            retrieval_k_expr,
            engine_expr,
        )
    )
    if reversal is not None:
        # OUTER: a result the selected event never touched has no reversal row
        # and keeps its current outcome through the COALESCE. An inner join
        # would silently drop every unchanged result and report each rate over
        # the movers alone -- 100% on a row where 19 of 487 moved.
        #
        # The current readings are joined only here, because only an as-of
        # request needs both eras: without one, `*_read` IS `*_now` and joining
        # them twice would multiply the rows.
        stmt = (
            stmt.join(reversal, reversal.c.result_id == Result.id, isouter=True)
            .join(exact_now, exact_now.c.result_id == Result.id, isouter=True)
            .join(got_facts_now, got_facts_now.c.result_id == Result.id, isouter=True)
        )

    if difficulty is not None:
        stmt = stmt.join(EvalItem, _item_join_clause()).where(
            EvalItem.item_metadata["difficulty"].astext == difficulty
        )

    rows = []
    for record in session.execute(stmt).all():
        graded = int(record.n_graded)
        rows.append(
            MatrixRowOut(
                row_key=_row_key(
                    record.solution_id,
                    record.model_id,
                    record.config_label,
                    record.config_digest,
                    record.engine,
                    record.retrieval_k,
                ),
                solution_id=record.solution_id,
                solution_name=record.solution_name,
                solution_version=record.solution_version,
                model_id=record.model_id,
                config_label=record.config_label,
                config_digest=record.config_digest,
                retrieval_k=(
                    int(record.retrieval_k) if str(record.retrieval_k or "").isdigit() else None
                ),
                arms=record.arms,
                # Only meaningful across repetitions; one run has no spread.
                ex_rate_min=(
                    float(record.ex_rate_min)
                    if int(record.n_runs) > 1 and record.ex_rate_min is not None
                    else None
                ),
                ex_rate_max=(
                    float(record.ex_rate_max)
                    if int(record.n_runs) > 1 and record.ex_rate_max is not None
                    else None
                ),
                engine=str(record.engine) or None,
                n_runs=int(record.n_runs),
                n_graded=graded,
                n_errors=int(record.n_errors),
                ex_rate=_rate(int(record.n_pass), graded),
                ex_target_engine_rate=(
                    _rate(int(record.n_pass_target_engine), graded)
                    if int(record.n_portability_flagged)
                    else None
                ),
                # Gated on whether the metric was read, never on whether it
                # was ever true. Guarding with the numerator made a row that
                # was scored and matched nothing report None -- "the grader
                # never ran" -- and the arms most likely to hit it are the
                # weak ones a reader is trying to tell apart.
                exact_rate=(
                    _rate(int(record.n_exact), graded) if int(record.n_exact_scored) else None
                ),
                got_facts_rate=(
                    _rate(int(record.n_got_facts), graded)
                    if int(record.n_got_facts_scored)
                    else None
                ),
                n_got_facts_scored=int(record.n_got_facts_scored),
                n_got_facts_subset_scored=int(record.n_got_facts_subset_scored),
                n_got_facts_full_scored=int(record.n_got_facts_full_scored),
                n_got_facts_scope_unknown=int(record.n_got_facts_scope_unknown),
                defer_rate=_rate(int(record.n_defer), graded),
                wrong_rate=_rate(int(record.n_fail), graded),
                current=(
                    None
                    if reversal is None
                    else MatrixCurrentOut(
                        ex_rate=_rate(int(record.n_pass_now), int(record.n_graded_now)),
                        defer_rate=_rate(int(record.n_defer_now), int(record.n_graded_now)),
                        wrong_rate=_rate(int(record.n_fail_now), int(record.n_graded_now)),
                        # Same None-vs-0.0 gate as the as-of side: a metric
                        # that was never read and one that matched nothing
                        # both count zero, and only one is a measurement.
                        exact_rate=(
                            _rate(int(record.n_exact_now), int(record.n_graded_now))
                            if int(record.n_exact_scored_now)
                            else None
                        ),
                        got_facts_rate=(
                            _rate(int(record.n_got_facts_now), int(record.n_graded_now))
                            if int(record.n_got_facts_scored_now)
                            else None
                        ),
                        n_graded=int(record.n_graded_now),
                    )
                ),
                affected=None if reversal is None else bool(int(record.n_touched_by_event)),
                changed_since=(None if reversal is None else bool(int(record.n_changed_since))),
                median_tokens=float(record.median_tokens)
                if record.median_tokens is not None
                else None,
                median_runtime_ms=float(record.median_runtime)
                if record.median_runtime is not None
                else None,
            )
        )
    # Sorted by the rate that is ON SCREEN, which under an as-of is the
    # rewound one. Sorting by today's number while showing last week's puts
    # the rows in an order the reader cannot derive from the column.
    rows.sort(key=lambda row: (row.ex_rate is None, -(row.ex_rate or 0.0)))

    # Facet counts over the unfiltered selection, so a filtered view still
    # shows what it is a slice of.
    #
    # DISTINCT items, not result rows. A bare count here counted one row per
    # graded attempt, so the facet read runs x items: a 135-question suite
    # showed "all 675" once five runs existed, and grew whenever anyone added
    # a run. The chip names the slice of the BENCHMARK the rates are over --
    # how many attempts backs each rate is n_graded's job, per row.
    level = sa.func.coalesce(EvalItem.item_metadata["difficulty"].astext, "unknown")
    facet_stmt = (
        sa.select(level, sa.func.count(sa.distinct(Result.item_id)))
        .select_from(Run)
        .join(Result, Result.run_id == Run.id)
        .join(EvalItem, _item_join_clause(), isouter=True)
        .where(*filters)
        .group_by(level)
    )
    difficulty_counts = {str(key): int(count) for key, count in session.execute(facet_stmt).all()}
    return MatrixOut(
        rows=rows,
        difficulty_counts=difficulty_counts,
        as_of=(
            None
            if event is None
            else MatrixAsOfOut(
                event_id=event.id,
                recorded_at=event.created_at,
                grader=event.grader,
                grader_version=event.grader_version,
                headline_metric=event.headline_metric,
            )
        ),
    )


@router.get(
    "/suites/{suite_id}/items",
    response_model=SuiteItemListOut,
    summary="The questions a benchmark scores against",
)
@requires(Permission.EVAL_VIEW)
def suite_items(
    suite_id: UUID,
    _actor: Annotated[
        User,
        Depends(require_permission(Permission.EVAL_VIEW, scope_kind="suite")),
    ],
    session: Annotated[Session, Depends(get_session)],
    difficulty: Annotated[str | None, Query()] = None,
    source: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> SuiteItemListOut:
    """List the suite's active items with difficulty and source facets.

    Items are matched by suite *name*: that is the key grading uses, and it is
    what makes the ten-item demo slice and the full corpus the same benchmark.
    Visibility is the suite's own team plus shared items.
    """
    suite = SuiteRepo(session).get(suite_id)
    assert suite is not None  # the permission dependency 404s first

    base = sa.select(EvalItem).where(
        EvalItem.valid_to.is_(None),
        EvalItem.suite == suite.name,
        sa.or_(EvalItem.team_id == suite.team_id, EvalItem.team_id.is_(None)),
    )
    items = list(session.scalars(base.order_by(EvalItem.question_hash, EvalItem.item_id)))

    difficulty_counts: dict[str, int] = {}
    source_counts: dict[str, int] = {}
    for item in items:
        level = str(item.item_metadata.get("difficulty") or "unknown")
        difficulty_counts[level] = difficulty_counts.get(level, 0) + 1
        origin = str(item.item_metadata.get("source") or "unknown")
        source_counts[origin] = source_counts.get(origin, 0) + 1

    selected = [
        item
        for item in items
        if (difficulty is None or str(item.item_metadata.get("difficulty")) == difficulty)
        and (source is None or str(item.item_metadata.get("source")) == source)
    ]
    page = selected[offset : offset + limit]

    def _meta_str(item: EvalItem, key: str) -> str | None:
        value = item.item_metadata.get(key)
        return str(value) if value is not None else None

    return SuiteItemListOut(
        items=[
            SuiteItemRowOut(
                item_id=item.item_id,
                question=str((item.item_input or {}).get("question") or ""),
                difficulty=_meta_str(item, "difficulty"),
                database=(
                    str((item.item_input or {}).get("db_id"))
                    if (item.item_input or {}).get("db_id") is not None
                    else None
                ),
                source=_meta_str(item, "source"),
                tolerance=(
                    dict(item.item_metadata["tolerance"])
                    if isinstance(item.item_metadata.get("tolerance"), dict)
                    else None
                ),
                has_gold_sql=bool((item.gold_answer or {}).get("sql")),
                gold_sql=(
                    str((item.gold_answer or {}).get("sql"))
                    if (item.gold_answer or {}).get("sql")
                    else None
                ),
            )
            for item in page
        ],
        total=len(selected),
        difficulty_counts=difficulty_counts,
        source_counts=source_counts,
    )
