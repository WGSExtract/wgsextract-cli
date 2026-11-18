"""Use a trusted app source checkout's plan and lifecycle expectations only."""

from __future__ import annotations

import argparse
import importlib.util
import sys
from collections.abc import Callable
from pathlib import Path

from gui_oracle import PLAN, GuiOracle

plan_path = PLAN
oracle_type = GuiOracle


def parse_arguments(parser: argparse.ArgumentParser) -> argparse.Namespace:
    parser.add_argument(
        "--app-contract",
        type=Path,
        help="Trusted release source checkout supplying app plan/step expectations; "
        "scientific and state checks remain those of this tree. Never use guest evidence.",
    )
    args = parser.parse_args()
    global plan_path, oracle_type
    plan_path, oracle_type = (
        load_contract(args.app_contract) if args.app_contract else (PLAN, GuiOracle)
    )
    return args


def load_contract(source: Path) -> tuple[Path, type[GuiOracle]]:
    """Keep today's scientific/state checks while matching released app steps.

    The caller must supply a trusted checkout, never collected guest evidence.
    Install Lab checks out the same immutable release revision as the installer.
    """
    scripts = source.resolve() / "scripts" / "installlab"
    plan = scripts / "gui" / "wgsextract-plan.json"
    oracle = scripts / "gui_oracle.py"
    if not plan.is_file() or not oracle.is_file():
        raise ValueError(f"App contract checkout lacks its plan/oracle: {scripts}")
    spec = importlib.util.spec_from_file_location("installlab_app_contract", oracle)
    if spec is None or spec.loader is None:
        raise ValueError(f"Cannot load app contract: {oracle}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    if module.PLAN.resolve() != plan:
        raise ValueError("App contract oracle must use its checkout's plan")

    def release_check(self: GuiOracle, check: Callable[[GuiOracle], None]) -> None:
        tree_platform = self.app
        try:
            self.app = module.app_platform(self.result_file("lab-run.json"))
            check(self)
        finally:
            self.app = tree_platform

    def check_steps(self: GuiOracle) -> None:
        release_check(self, module.GuiOracle.check_steps)

    def check_app_reports(self: GuiOracle) -> None:
        release_check(self, module.GuiOracle.check_app_reports)

    # Only lifecycle/plan expectations follow the tag. In particular, never
    # replace check_outputs or the inherited biological accuracy checks.
    contract_oracle = type(
        "AppContractOracle",
        (GuiOracle,),
        {
            "check_steps": check_steps,
            "check_app_reports": check_app_reports,
        },
    )
    return plan, contract_oracle
