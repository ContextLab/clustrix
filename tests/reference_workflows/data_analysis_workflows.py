"""
Reference patterns for data analysis workflows.

These patterns demonstrate realistic data science and analysis tasks
that users would perform with clustrix, including pandas operations,
numpy computations, and machine learning workflows.
"""

import pytest
import os
import tempfile
import json
from pathlib import Path
from clustrix import cluster
from clustrix.config import ClusterConfig
import clustrix.config as config_module


def test_pandas_analysis_workflow():
    """
    Reference pattern for pandas-based data analysis.

    This demonstrates:
    - Real-world pandas operations
    - Data cleaning and transformation
    - Aggregation and grouping
    - Memory-efficient processing
    """

    config = ClusterConfig()
    config.cluster_type = os.getenv("TEST_CLUSTER_TYPE", "local")
    if config.cluster_type != "local":
        config.cluster_host = os.getenv("TEST_CLUSTER_HOST")
        config.username = os.getenv("TEST_CLUSTER_USER")

    original_config = config_module._config
    config_module._config = config

    try:

        @cluster(cores=4, memory="8GB", parallel=False)
        def analyze_sales_data(data_dict):
            """Analyze sales data - common business analytics task."""
            import pandas as pd
            import numpy as np
            from datetime import datetime, timedelta

            # Convert dict to DataFrame (simulating reading from file/database)
            df = pd.DataFrame(data_dict)

            # Data cleaning
            df["date"] = pd.to_datetime(df["date"])
            df["amount"] = pd.to_numeric(df["amount"], errors="coerce")
            df = df.dropna(subset=["amount"])

            # Feature engineering
            df["year"] = df["date"].dt.year
            df["month"] = df["date"].dt.month
            df["quarter"] = df["date"].dt.quarter
            df["day_of_week"] = df["date"].dt.dayofweek
            df["is_weekend"] = df["day_of_week"].isin([5, 6])

            # Aggregations
            results = {
                "total_sales": float(df["amount"].sum()),
                "average_sale": float(df["amount"].mean()),
                "median_sale": float(df["amount"].median()),
                "total_transactions": len(df),
                # Group by analysis
                "sales_by_category": df.groupby("category")["amount"].sum().to_dict(),
                "sales_by_region": df.groupby("region")["amount"].sum().to_dict(),
                "monthly_sales": df.groupby("month")["amount"].sum().to_dict(),
                # Advanced analytics
                "top_products": df.groupby("product")["amount"]
                .sum()
                .nlargest(5)
                .to_dict(),
                "weekend_vs_weekday": {
                    "weekend_avg": float(df[df["is_weekend"]]["amount"].mean()),
                    "weekday_avg": float(df[~df["is_weekend"]]["amount"].mean()),
                },
                # Statistical measures
                "sales_std": float(df["amount"].std()),
                "sales_skew": float(df["amount"].skew()),
                "sales_kurtosis": float(df["amount"].kurtosis()),
                # Time series insights
                "trend": (
                    "increasing"
                    if df.groupby("date")["amount"].sum().is_monotonic_increasing
                    else "variable"
                ),
            }

            # Correlation analysis
            numeric_cols = df.select_dtypes(include=[np.number]).columns
            if len(numeric_cols) > 1:
                corr_matrix = df[numeric_cols].corr()
                results["correlations"] = corr_matrix.to_dict()

            return results

        # Create realistic test data
        import random
        from datetime import datetime, timedelta

        base_date = datetime(2024, 1, 1)
        test_data = {
            "date": [(base_date + timedelta(days=i)).isoformat() for i in range(100)],
            "amount": [random.uniform(10, 1000) for _ in range(100)],
            "product": [random.choice(["A", "B", "C", "D", "E"]) for _ in range(100)],
            "category": [
                random.choice(["Electronics", "Clothing", "Food"]) for _ in range(100)
            ],
            "region": [
                random.choice(["North", "South", "East", "West"]) for _ in range(100)
            ],
        }

        # Execute analysis
        results = analyze_sales_data(test_data)

        # Validate results
        assert results["total_transactions"] == 100
        assert results["total_sales"] > 0
        assert results["average_sale"] > 0
        assert len(results["sales_by_category"]) <= 3
        assert len(results["sales_by_region"]) <= 4
        assert len(results["top_products"]) <= 5
        assert results["weekend_vs_weekday"]["weekend_avg"] > 0
        assert results["sales_std"] > 0

    finally:
        config_module._config = original_config


def test_numpy_computation_workflow():
    """
    Reference pattern for numpy-based scientific computation.

    This demonstrates:
    - Numerical computations
    - Linear algebra operations
    - Signal processing
    - Statistical analysis
    """

    config = ClusterConfig()
    config.cluster_type = os.getenv("TEST_CLUSTER_TYPE", "local")

    original_config = config_module._config
    config_module._config = config

    try:

        @cluster(cores=8, memory="16GB")
        def scientific_computation(signal_length, sampling_rate=1000):
            """Perform scientific signal processing and analysis."""
            import numpy as np
            from scipy import signal, fft
            from scipy.stats import norm, kstest
            import time

            start_time = time.time()

            # Generate synthetic signal with noise
            t = np.linspace(0, signal_length, signal_length * sampling_rate)

            # Create complex signal: sum of sinusoids + noise
            freq1, freq2, freq3 = 50, 120, 250  # Hz
            clean_signal = (
                np.sin(2 * np.pi * freq1 * t)
                + 0.5 * np.sin(2 * np.pi * freq2 * t)
                + 0.3 * np.sin(2 * np.pi * freq3 * t)
            )
            noise = np.random.normal(0, 0.5, len(t))
            noisy_signal = clean_signal + noise

            # Perform FFT
            fft_vals = fft.fft(noisy_signal)
            fft_freqs = fft.fftfreq(len(noisy_signal), 1 / sampling_rate)

            # Find dominant frequencies
            fft_magnitude = np.abs(fft_vals)[: len(fft_vals) // 2]
            fft_freqs_positive = fft_freqs[: len(fft_freqs) // 2]
            peak_indices = signal.find_peaks(fft_magnitude, height=len(t) / 10)[0]
            dominant_freqs = fft_freqs_positive[peak_indices].tolist()

            # Apply filters
            # Low-pass filter
            sos = signal.butter(10, 100, "lowpass", fs=sampling_rate, output="sos")
            filtered_low = signal.sosfilt(sos, noisy_signal)

            # High-pass filter
            sos_high = signal.butter(10, 30, "highpass", fs=sampling_rate, output="sos")
            filtered_high = signal.sosfilt(sos_high, noisy_signal)

            # Statistical analysis
            stats_results = {
                "signal_mean": float(np.mean(noisy_signal)),
                "signal_std": float(np.std(noisy_signal)),
                "signal_max": float(np.max(noisy_signal)),
                "signal_min": float(np.min(noisy_signal)),
                "signal_rms": float(np.sqrt(np.mean(noisy_signal**2))),
                "snr_db": float(10 * np.log10(np.var(clean_signal) / np.var(noise))),
            }

            # Test for normality
            _, p_value = kstest(noise, "norm", args=(np.mean(noise), np.std(noise)))
            stats_results["noise_is_normal"] = p_value > 0.05

            # Autocorrelation
            autocorr = np.correlate(
                noisy_signal[:1000], noisy_signal[:1000], mode="full"
            )
            autocorr = autocorr[len(autocorr) // 2 :]
            autocorr = autocorr / autocorr[0]  # Normalize

            computation_time = time.time() - start_time

            return {
                "signal_length_seconds": signal_length,
                "sampling_rate": sampling_rate,
                "dominant_frequencies": dominant_freqs[:5],  # Top 5 frequencies
                "statistics": stats_results,
                "autocorrelation_peak": float(np.max(autocorr[1:])),
                "filtered_low_power": float(np.mean(filtered_low**2)),
                "filtered_high_power": float(np.mean(filtered_high**2)),
                "computation_time": computation_time,
            }

        # Execute computation
        results = scientific_computation(2, sampling_rate=1000)  # 2 second signal

        # Validate results
        assert results["signal_length_seconds"] == 2
        assert results["sampling_rate"] == 1000
        assert len(results["dominant_frequencies"]) > 0
        assert results["statistics"]["snr_db"] < 10  # Should have some noise
        assert results["computation_time"] > 0
        assert results["filtered_low_power"] > 0
        assert results["filtered_high_power"] > 0

    finally:
        config_module._config = original_config


def test_machine_learning_workflow():
    """
    Reference pattern for machine learning workflows.

    This demonstrates:
    - Real ML model training
    - Cross-validation
    - Hyperparameter tuning
    - Model evaluation
    """

    config = ClusterConfig()
    config.cluster_type = os.getenv("TEST_CLUSTER_TYPE", "local")

    original_config = config_module._config
    config_module._config = config

    try:

        @cluster(cores=8, memory="16GB")
        def train_and_evaluate_models(n_samples, n_features, test_size=0.2):
            """Train multiple ML models and compare performance."""
            import numpy as np
            from sklearn.datasets import make_classification
            from sklearn.model_selection import (
                train_test_split,
                cross_val_score,
                GridSearchCV,
            )
            from sklearn.preprocessing import StandardScaler
            from sklearn.ensemble import (
                RandomForestClassifier,
                GradientBoostingClassifier,
            )
            from sklearn.svm import SVC
            from sklearn.metrics import (
                accuracy_score,
                precision_score,
                recall_score,
                f1_score,
                roc_auc_score,
            )
            import time

            start_time = time.time()

            # Generate synthetic dataset
            X, y = make_classification(
                n_samples=n_samples,
                n_features=n_features,
                n_informative=n_features // 2,
                n_redundant=n_features // 4,
                n_clusters_per_class=2,
                random_state=42,
            )

            # Split and scale data
            X_train, X_test, y_train, y_test = train_test_split(
                X, y, test_size=test_size, random_state=42, stratify=y
            )

            scaler = StandardScaler()
            X_train_scaled = scaler.fit_transform(X_train)
            X_test_scaled = scaler.transform(X_test)

            results = {
                "dataset": {
                    "n_samples": n_samples,
                    "n_features": n_features,
                    "train_size": len(X_train),
                    "test_size": len(X_test),
                    "class_balance": float(np.mean(y)),
                },
                "models": {},
            }

            # Train Random Forest with hyperparameter tuning
            rf_params = {
                "n_estimators": [50, 100],
                "max_depth": [5, 10, None],
                "min_samples_split": [2, 5],
            }
            rf_grid = GridSearchCV(
                RandomForestClassifier(random_state=42),
                rf_params,
                cv=3,
                scoring="f1",
                n_jobs=-1,
            )
            rf_grid.fit(X_train_scaled, y_train)
            rf_best = rf_grid.best_estimator_

            # Evaluate Random Forest
            rf_pred = rf_best.predict(X_test_scaled)
            rf_prob = rf_best.predict_proba(X_test_scaled)[:, 1]

            results["models"]["random_forest"] = {
                "best_params": rf_grid.best_params_,
                "cv_score": float(rf_grid.best_score_),
                "test_accuracy": float(accuracy_score(y_test, rf_pred)),
                "test_precision": float(precision_score(y_test, rf_pred)),
                "test_recall": float(recall_score(y_test, rf_pred)),
                "test_f1": float(f1_score(y_test, rf_pred)),
                "test_auc": float(roc_auc_score(y_test, rf_prob)),
                "feature_importance": rf_best.feature_importances_[
                    :5
                ].tolist(),  # Top 5
            }

            # Train Gradient Boosting
            gb_model = GradientBoostingClassifier(
                n_estimators=100, learning_rate=0.1, max_depth=3, random_state=42
            )
            gb_model.fit(X_train_scaled, y_train)
            gb_pred = gb_model.predict(X_test_scaled)
            gb_prob = gb_model.predict_proba(X_test_scaled)[:, 1]

            results["models"]["gradient_boosting"] = {
                "test_accuracy": float(accuracy_score(y_test, gb_pred)),
                "test_precision": float(precision_score(y_test, gb_pred)),
                "test_recall": float(recall_score(y_test, gb_pred)),
                "test_f1": float(f1_score(y_test, gb_pred)),
                "test_auc": float(roc_auc_score(y_test, gb_prob)),
            }

            # Cross-validation comparison
            cv_scores_rf = cross_val_score(
                rf_best, X_train_scaled, y_train, cv=5, scoring="f1"
            )
            cv_scores_gb = cross_val_score(
                gb_model, X_train_scaled, y_train, cv=5, scoring="f1"
            )

            results["cross_validation"] = {
                "random_forest_mean": float(np.mean(cv_scores_rf)),
                "random_forest_std": float(np.std(cv_scores_rf)),
                "gradient_boosting_mean": float(np.mean(cv_scores_gb)),
                "gradient_boosting_std": float(np.std(cv_scores_gb)),
            }

            # Determine best model
            if (
                results["models"]["random_forest"]["test_f1"]
                > results["models"]["gradient_boosting"]["test_f1"]
            ):
                results["best_model"] = "random_forest"
            else:
                results["best_model"] = "gradient_boosting"

            results["training_time"] = time.time() - start_time

            return results

        # Execute ML workflow
        results = train_and_evaluate_models(1000, 20, test_size=0.2)

        # Validate results
        assert results["dataset"]["n_samples"] == 1000
        assert results["dataset"]["n_features"] == 20
        assert results["dataset"]["train_size"] == 800
        assert results["dataset"]["test_size"] == 200

        # Check model performance
        assert results["models"]["random_forest"]["test_accuracy"] > 0.5
        assert results["models"]["random_forest"]["test_f1"] > 0.5
        assert len(results["models"]["random_forest"]["feature_importance"]) == 5

        assert results["models"]["gradient_boosting"]["test_accuracy"] > 0.5
        assert results["models"]["gradient_boosting"]["test_f1"] > 0.5

        # Check cross-validation
        assert results["cross_validation"]["random_forest_mean"] > 0.5
        assert results["cross_validation"]["gradient_boosting_mean"] > 0.5

        assert results["best_model"] in ["random_forest", "gradient_boosting"]
        assert results["training_time"] > 0

    finally:
        config_module._config = original_config
