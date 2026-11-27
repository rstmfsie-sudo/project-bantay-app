# Machine learning in PROJECT B.A.N.T.A.Y.: dataset and models

## What the models learn
- **Task:** classify each tracked object from the 2D LiDAR into one of the **categories**
  `car`, `motorcycle`, `bicycle`, `pedestrian`.
- **`unknown` is not a learned class.** It is the answer when the model's confidence is below the threshold
  (Settings: `ml_min_confidence`, default 0.6).
- **Features: 8 measurements of the object itself.** Length, width, linearity, number of segments (gaps),
  laser coverage, intensity, the largest length while tracked, and speed.
- **Excluded on purpose:** distance and location. They describe the site rather than the object, so a model
  that learned them would fail at another road.
- **Two models:**
  - **Random Forest** (scikit-learn) on the 8 measurements [Breiman 2001].
  - **PointNet-style neural network** (PyTorch) on the object's raw 2D points plus the measurements [Qi et al. 2017].

## The dataset has two partitions, never mixed
| Partition | Where | What |
|---|---|---|
| **Real** | SQLite `samples` table | Objects **you** labeled in Live Scan (Follow mode, or from a recording) |
| **Synthetic** | `datasets/twin_dataset.joblib` (+ `.json`) | Digital-twin scans: the simulated T-mini Plus seeing 3D models at your mounting height |

- Synthetic object IDs start with `twin:`. Real object IDs are `<session>:<track>`.
- The synthetic partition is versioned (`version`, `generator`, `seed`, mounting height, class counts) and
  exportable (`/api/dataset/synthetic.csv`).
- Real data exports as CSV and JSON from the Dataset Manager.

## Rules followed, and why
1. **Split by object, never by frame.** Frames of one passing car are near-duplicates. If some went to
   training and others to testing, the score would be inflated by leakage [Kaufman et al. 2012]. Every
   evaluation uses `StratifiedGroupKFold` grouped by object [scikit-learn User Guide §3.1, "Cross-validation
   iterators for grouped data"].
2. **Report what was measured, on what.**
   - Every model records what it **was trained on** (`twin`, `real` or `both`), its classes, sample and object
     counts per class, confusion matrix, per-class precision/recall/F1, and its version.
   - This follows the "model card" idea [Mitchell et al. 2019] and the documentation of a dataset's origin and
     composition [Gebru et al. 2021].
3. **Simulation is a starting point, not a result.**
   - Training on the twin follows sim-to-real practice: randomized object sizes, reflectivity (range),
     dropouts and noise [Tobin et al. 2017].
   - A twin-only score says nothing about real roads, so twin-trained results are labelled
     **"ML (digital twin)"** in the app.
   - When real labels exist, train on **Both**. The app then reports accuracy on the **real objects alone**,
     each held out of its fold. That is the number to trust and to put in the research paper.
4. **Don't learn what the simulator can't produce.** The twin does not model laser intensity (it is a constant),
   so `mean_intensity` is dropped whenever synthetic data is used. Otherwise a model could learn
   "intensity = 100 means simulated".
5. **Balance and minimums.**
   - Random Forest: at least 2 categories with 3+ objects (and 5+ samples) each.
   - Neural network: 10+ objects per category.
   - Both use class-balanced training. Aim for 30+ *different* real objects per category, across distances,
     directions and speeds.
6. **Validation is a human decision.** "Validated" is set by the researcher only, after testing on new data
   from a different day or site.

## How to run it (in the app)
- **Machine Learning page, or Digital Twin page → "Train and switch to the better model".**
- **Learn from:**
  - **Digital twin:** generates the synthetic partition if needed. 60 pretend objects per category is the default.
  - **My real labels:** available once you have labeled objects.
  - **Both:** twin data plus your labels.
- **What happens:** both models train in the background. The app switches to the one with the better score:
  on real objects when they took part, otherwise on simulated objects.
- **API:**
  ```
  POST /api/ml/train {"data": "twin"|"real"|"both", "models": ["rf","nn"], "height_m": 0.6, "objects_per_class": 60, "activate": true}
  ```

## References
- Breiman, L. (2001). Random Forests. *Machine Learning*, 45(1), 5–32.
- Gebru, T., et al. (2021). Datasheets for Datasets. *Communications of the ACM*, 64(12). arXiv:1803.09010.
- Kaufman, S., Rosset, S., Perlich, C., & Stitelman, O. (2012). Leakage in data mining: formulation, detection,
  and avoidance. *ACM Transactions on Knowledge Discovery from Data*, 6(4).
- Mitchell, M., et al. (2019). Model Cards for Model Reporting. *FAT\* '19*. arXiv:1810.03993.
- Qi, C. R., Su, H., Mo, K., & Guibas, L. J. (2017). PointNet: Deep Learning on Point Sets for 3D Classification
  and Segmentation. *CVPR 2017*. arXiv:1612.00593.
- scikit-learn developers. User Guide, §3.1 "Cross-validation: evaluating estimator performance".
  https://scikit-learn.org/stable/modules/cross_validation.html
- Tobin, J., et al. (2017). Domain Randomization for Transferring Deep Neural Networks from Simulation to the
  Real World. *IROS 2017*. arXiv:1703.06907.
