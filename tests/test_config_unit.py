"""
Unit tests for application configuration.

Verifies there is no duplicate model_config (Bug 5 fix).
"""

import ast


def _get_file_source(path):
    with open(path, "r") as f:
        return f.read()


class TestConfigNoDuplicate:
    def test_no_duplicate_model_config(self):
        """Bug 5: model_config must not be defined twice in config.py."""
        source = _get_file_source("app/config.py")
        tree = ast.parse(source)

        model_config_assignments = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == "model_config":
                        model_config_assignments.append(node.lineno)

        assert len(model_config_assignments) == 1, (
            f"Expected exactly 1 model_config assignment, "
            f"found {len(model_config_assignments)} at lines {model_config_assignments}"
        )
