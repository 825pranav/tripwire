"""Vectorised Isolation Forest scoring for small batches.

sklearn's `score_samples` walks the trees one by one through joblib, which costs a few
milliseconds per call regardless of batch size. The streaming scorer calls it with a
handful of windows at a time, so that fixed cost dominated (about 40% of rules + ML time
in the profile). This packs every tree into flat arrays and walks all trees for all rows
together with numpy, giving the same scores as sklearn (checked in tests/test_ml.py).
"""

from __future__ import annotations

import numpy as np
from sklearn.ensemble import IsolationForest


def average_path_length(n: np.ndarray) -> np.ndarray:
    """Expected path length of an unsuccessful BST search over n samples (as sklearn)."""
    n = np.asarray(n, dtype=np.float64)
    out = np.zeros_like(n)
    out[n == 2] = 1.0
    big = n > 2
    out[big] = 2.0 * (np.log(n[big] - 1.0) + np.euler_gamma) - 2.0 * (n[big] - 1.0) / n[big]
    return out


class FastForest:
    def __init__(self, forest: IsolationForest) -> None:
        lefts, rights, features, thresholds, values, roots = [], [], [], [], [], []
        offset = 0
        max_depth = 0
        for tree, columns in zip(forest.estimators_, forest.estimators_features_, strict=True):
            t = tree.tree_
            n = t.node_count
            leaf = t.children_left == -1
            nodes = np.arange(n)
            lefts.append(np.where(leaf, nodes, t.children_left) + offset)
            rights.append(np.where(leaf, nodes, t.children_right) + offset)
            # tree features index the tree's own column subset; map back to input columns
            features.append(np.where(leaf, 0, np.asarray(columns)[np.maximum(t.feature, 0)]))
            thresholds.append(np.where(leaf, 0.0, t.threshold))
            values.append(t.compute_node_depths() + average_path_length(t.n_node_samples) - 1.0)
            roots.append(offset)
            offset += n
            max_depth = max(max_depth, t.max_depth)
        self.left = np.concatenate(lefts)
        self.right = np.concatenate(rights)
        self.feature = np.concatenate(features)
        self.threshold = np.concatenate(thresholds)
        self.value = np.concatenate(values)
        self.roots = np.array(roots)
        self.max_depth = max_depth
        self.denominator = len(roots) * float(
            average_path_length(np.array([forest._max_samples]))[0]
        )

    def scores(self, x: np.ndarray) -> np.ndarray:
        """Same as `-IsolationForest.score_samples(x)`: higher is more anomalous."""
        x = np.asarray(x, dtype=np.float32).astype(np.float64)  # trees compare in float32
        rows = np.arange(x.shape[0])[None, :]
        node = np.repeat(self.roots[:, None], x.shape[0], axis=1)
        for _ in range(self.max_depth):
            go_left = x[rows, self.feature[node]] <= self.threshold[node]
            node = np.where(go_left, self.left[node], self.right[node])
        depths = self.value[node].sum(axis=0)
        return np.power(2.0, -depths / self.denominator)
