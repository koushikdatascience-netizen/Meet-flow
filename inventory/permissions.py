"""Independent actions; legacy grants expand without granting additional access."""

FEATURE_GROUPS = [
    (label, [(f"{key}_{action}", action.title()) for action in ("add", "edit", "delete")])
    for key, label in [("items", "Item Master"), ("purchase", "Purchase"), ("sale", "Sale / stock out"), ("opening", "Opening stock")]
] + [("Reports", [("reports_view", "View"), ("reports_download", "Download / print")])]
ALL_FEATURES = [key for _, choices in FEATURE_GROUPS for key, _ in choices]


def expand_features(features):
    result = set(features or []) & set(ALL_FEATURES)
    for module in ("opening", "purchase", "sale"):
        if module in (features or []):
            result.add(f"{module}_add")
            for action in ("edit", "delete"):
                if action in features:
                    result.add(f"{module}_{action}")
    if "reports" in (features or []):
        result.update(["reports_view", "reports_download"])
    return [feature for feature in ALL_FEATURES if feature in result]
