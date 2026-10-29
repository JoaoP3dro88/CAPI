"""
cycle_time_utils.py
===================
Utilitário compartilhado para lookup de tempo de ciclo com fallback por PN mais próximo.

Usado por:
  - simulation.py  (Restrictions.get_cycle_time)
  - data_gathering.py (fetch_machine_oee)
  - snapshot_now.py  (_enrich_machines)
"""
from __future__ import annotations

from difflib import SequenceMatcher
from typing import Dict, Optional, Tuple


def closest_cycle_time(
    part_number_id: str,
    candidates: Dict[str, float],
) -> Tuple[Optional[float], Optional[str]]:
    """
    Retorna o tempo de ciclo do TypeNumber mais parecido com `part_number_id`
    dentro de `candidates` (dict pn → secs).

    Critério de proximidade:
      1. Maior prefixo comum (caracteres iniciais idênticos)
      2. Em empate, maior ratio de similaridade (SequenceMatcher)

    Retorna (secs, pn_usado) ou (None, None) se `candidates` estiver vazio.
    """
    if not candidates:
        return None, None

    best_pn: Optional[str] = None
    best_prefix = -1
    best_ratio  = -1.0

    for candidate_pn, secs in candidates.items():
        if not secs or secs <= 0:
            continue

        # Comprimento do prefixo comum
        prefix_len = 0
        for a, b in zip(part_number_id, candidate_pn):
            if a == b:
                prefix_len += 1
            else:
                break

        ratio = SequenceMatcher(None, part_number_id, candidate_pn).ratio()

        if prefix_len > best_prefix or (prefix_len == best_prefix and ratio > best_ratio):
            best_prefix = prefix_len
            best_ratio  = ratio
            best_pn     = candidate_pn

    if best_pn is None:
        return None, None
    return candidates[best_pn], best_pn


def lookup_cycle_time(
    part_number_id: str,
    machine_id: str,
    cycle_times: Dict[Tuple[str, str], float],
    *,
    use_fallback: bool = True,
) -> Tuple[float, Optional[str]]:
    """
    Lookup completo de tempo de ciclo para (machine_id, part_number_id).

    Hierarquia:
      1. Correspondência exata (machine_id, part_number_id)
      2. [se use_fallback] PN mais próximo para o mesmo machine_id
      3. [se use_fallback] PN mais próximo em qualquer máquina (último recurso)

    Retorna (secs, fallback_pn_usado).
    - fallback_pn_usado é None se houve correspondência exata.
    - Lança ValueError se nenhum ciclo for encontrado mesmo com fallback.
    """
    # 1. Exato
    exact = cycle_times.get((machine_id, part_number_id))
    if exact and exact > 0:
        return exact, None

    if not use_fallback:
        raise ValueError(
            f"Missing cycle_time for machine_id={machine_id} part_number_id={part_number_id}"
        )

    # 2. PN mais próximo na mesma máquina
    same_machine: Dict[str, float] = {
        pn: secs
        for (mid, pn), secs in cycle_times.items()
        if mid == machine_id and secs and secs > 0
    }
    secs, fallback_pn = closest_cycle_time(part_number_id, same_machine)
    if secs is not None:
        return secs, fallback_pn

    # 3. PN mais próximo em qualquer máquina (último recurso)
    all_pns: Dict[str, float] = {}
    for (_, pn), s in cycle_times.items():
        if s and s > 0:
            # média se o mesmo PN aparecer em múltiplas máquinas
            if pn not in all_pns:
                all_pns[pn] = s

    secs, fallback_pn = closest_cycle_time(part_number_id, all_pns)
    if secs is not None:
        return secs, fallback_pn

    raise ValueError(
        f"No cycle_time found (even with fallback) for machine_id={machine_id} "
        f"part_number_id={part_number_id}"
    )
