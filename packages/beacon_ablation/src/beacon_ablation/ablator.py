"""SolutionConfig enumerator for ablation sweeps."""

from __future__ import annotations

from typing import Any, Protocol

from beacon_ablation.errors import InvalidConfigurationError, LayerNotDeclaredError


class _HasLayers(Protocol):
    def layers(self) -> list[Any]:
        """Return the SUT's declared layer descriptors."""
        ...


class _HasModelCopy(Protocol):
    layers_enabled: dict[str, bool]

    def model_copy(self, *, deep: bool = False) -> _HasModelCopy:
        """Return a (deep) copy of the config so its layers_enabled can be mutated."""
        ...


class Ablator:
    """Enumerates SolutionConfig permutations for substrate ablation sweeps."""

    def enumerate_loo_configs(
        self,
        base_config: _HasModelCopy,
        *,
        sut: _HasLayers,
    ) -> list[tuple[str, _HasModelCopy]]:
        """Return labeled configs for a leave-one-out sweep."""
        layers_enabled = dict(base_config.layers_enabled)
        if not layers_enabled:
            raise InvalidConfigurationError(
                "base_config has no layers configured; nothing to ablate"
            )

        declared = {layer.name for layer in sut.layers()}
        for name in layers_enabled:
            if name not in declared:
                raise LayerNotDeclaredError(
                    f"layer '{name}' not declared by SUT; declared layers: {sorted(declared)}"
                )

        out: list[tuple[str, _HasModelCopy]] = [("baseline", base_config)]
        for layer_name, is_on in layers_enabled.items():
            if not is_on:
                continue
            ablated = base_config.model_copy(deep=True)
            ablated.layers_enabled[layer_name] = False
            out.append((f"no_{layer_name}", ablated))

        return out
