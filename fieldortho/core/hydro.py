"""Drainage analysis for river flooding: priority-flood, flow accumulation, HAND.

HAND (Height Above Nearest Drainage; Renno et al. 2008, Nobre et al. 2011) gives, for
every cell, its height above the river cell it drains to. A river that rises by h
metres floods every connected cell with HAND < h, to depth h - HAND - i.e. the water
surface follows the river's own slope instead of being one flat level.

Flow routing uses the Priority-Flood algorithm (Barnes et al. 2014), which handles
pits and flats: each cell drains to the neighbour it was reached from, so the result
is always a valid drainage tree ending at the map edge.
"""
from __future__ import annotations

import heapq
from dataclasses import dataclass

import numpy as np

from .terrain import Dem

_NB = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]


@dataclass
class Drainage:
    order: np.ndarray        # flat cell indices in processing order (outlets first)
    parent: np.ndarray       # flat index of the downstream cell (-1 = outlet)
    acc: np.ndarray          # contributing area in cells (2-D)
    filled: np.ndarray       # depression-filled elevations (2-D)


def drainage(dem: Dem) -> Drainage:
    z = dem.z
    H, W = z.shape
    valid = np.isfinite(z)
    zf = np.where(valid, z, np.inf)
    N = H * W
    parent = np.full(N, -1, dtype=np.int64)
    done = np.zeros(N, dtype=bool)
    filled = np.full(N, np.nan)
    zflat = zf.ravel()
    heap = []
    # outlets: valid cells on the map edge or next to no-data
    from scipy import ndimage as ndi
    edge = np.zeros_like(valid)
    edge[0, :] = edge[-1, :] = edge[:, 0] = edge[:, -1] = True
    near_nd = ndi.binary_dilation(~valid, structure=np.ones((3, 3), bool))
    outlets = np.nonzero((valid & (edge | near_nd)).ravel())[0]
    for i in outlets.tolist():
        heap.append((zflat[i], i))
        done[i] = True
        filled[i] = zflat[i]
    heapq.heapify(heap)
    order = []
    push, pop = heapq.heappush, heapq.heappop
    while heap:
        e, i = pop(heap)
        order.append(i)
        r, c = divmod(i, W)
        for dr, dc in _NB:
            r2, c2 = r + dr, c + dc
            if 0 <= r2 < H and 0 <= c2 < W:
                j = r2 * W + c2
                if done[j] or not valid[r2, c2]:
                    continue
                done[j] = True
                parent[j] = i
                e2 = zflat[j] if zflat[j] > e else e
                filled[j] = e2
                push(heap, (e2, j))
    order = np.array(order, dtype=np.int64)
    acc = np.ones(N, dtype=np.float64)
    acc[~valid.ravel()] = 0
    par = parent
    for i in order[::-1].tolist():          # upstream first
        p = par[i]
        if p >= 0:
            acc[p] += acc[i]
    return Drainage(order, parent, acc.reshape(H, W), filled.reshape(H, W))


def hand(dem: Dem, dr: Drainage, streams: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """HAND (m) and the flat index of each cell's drainage (stream) cell (-1 if none)."""
    H, W = dem.shape
    z = dem.z.ravel()
    s = streams.ravel()
    ref = np.full(H * W, -1, dtype=np.int64)
    par = dr.parent
    for i in dr.order.tolist():             # downstream first
        if s[i]:
            ref[i] = i
        else:
            p = par[i]
            if p >= 0:
                ref[i] = ref[p]
    h = np.full(H * W, np.nan)
    ok = ref >= 0
    h[ok] = z[ok] - z[ref[ok]]
    return h.reshape(H, W), ref.reshape(H, W)


def river_network(dr: Drainage, seed: tuple[int, int], fraction: float = 0.3, snap: int = 6) -> np.ndarray:
    """Stream cells of the river at `seed`: the main channel through the clicked point
    plus channels of similar size (contributing area >= fraction * area at the click)."""
    from scipy import ndimage as ndi
    acc = dr.acc
    H, W = acc.shape
    r, c = seed
    r0, r1, c0, c1 = max(0, r - snap), min(H, r + snap + 1), max(0, c - snap), min(W, c + snap + 1)
    win = acc[r0:r1, c0:c1]
    k = np.unravel_index(int(np.argmax(win)), win.shape)
    sr, sc = r0 + k[0], c0 + k[1]
    a_seed = acc[sr, sc]
    thr = max(50.0, fraction * a_seed)
    streams = acc >= thr
    lab, _n = ndi.label(streams, structure=np.ones((3, 3), bool))
    keep = lab == lab[sr, sc] if lab[sr, sc] else np.zeros_like(streams)
    # follow the main stem upstream (largest tributary at each junction) to its head
    i = sr * W + sc
    min_acc = max(25.0, 0.02 * a_seed)
    while True:
        r, c = divmod(i, W)
        best, best_a = -1, 0.0
        for dr_, dc_ in _NB:
            r2, c2 = r + dr_, c + dc_
            if 0 <= r2 < H and 0 <= c2 < W:
                j = r2 * W + c2
                if dr.parent[j] == i and acc[r2, c2] > best_a:
                    best, best_a = j, acc[r2, c2]
        if best < 0 or best_a < min_acc:
            break
        keep.flat[best] = True
        i = best
    # always include everything downstream of the click (to the sea)
    i = sr * W + sc
    while i >= 0:
        keep.flat[i] = True
        i = dr.parent[i]
    return keep, (sr, sc), a_seed
