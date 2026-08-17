"""novel-agent CLI 包

原 agent/cli.py 巨型单体已拆分为 cli/app.py + cli/commands/*。
对外仍暴露 `app`（供 `python -m agent.cli` 与测试使用）。
"""

from agent.cli._app import app, console
import agent.cli.commands  # noqa: F401  副作用：注册所有命令

# 向后兼容：测试通过 `from agent.cli import app / mode` 等访问
from agent.cli.commands.version import version
from agent.cli.commands.start import start
from agent.cli.commands.discuss import discuss
from agent.cli.commands.architecture import architecture
from agent.cli.commands.confirm_architecture import confirm_architecture
from agent.cli.commands.outline import outline
from agent.cli.commands.design_characters import design_characters
from agent.cli.commands.write import write
from agent.cli.commands.adjust_route import adjust_route
from agent.cli.commands.adjust_relation import adjust_relation
from agent.cli.commands.mode import mode
from agent.cli.commands.foreshadow_report import foreshadow_report
from agent.cli.commands.foreshadow_check import foreshadow_check
from agent.cli.commands.snapshot import snapshot
from agent.cli.commands.list_snapshots import list_snapshots
from agent.cli.commands.rollback_setting import rollback_setting
from agent.cli.commands.frozen_fields import frozen_fields
from agent.cli.commands.unfreeze import unfreeze
from agent.cli.commands.status import status
from agent.cli.commands.load_skill import load_skill
from agent.cli.commands.bookworm_review import bookworm_review
from agent.cli.commands.help_ import help_
from agent.cli.commands.reset_state import reset_state
from agent.cli.commands.draft_status import draft_status
from agent.cli.commands.draft_discard import draft_discard
from agent.cli.commands.rollback import rollback
from agent.cli.commands.resume import resume
from agent.cli.commands.export import export
from agent.cli.commands.import_draft import import_draft
from agent.cli.commands.completion_extras import completion_extras
from agent.cli.commands.audit_setting import audit_setting
from agent.cli.commands.audit_chapter import audit_chapter
from agent.cli.commands.summarize_chapter import summarize_chapter
from agent.cli.commands.summarize_range import summarize_range
from agent.cli.commands.context import context
from agent.cli.commands.list_genres import list_genres
from agent.cli.commands.genre_info import genre_info
from agent.cli.commands.load_genre import load_genre
from agent.cli.commands.inject_genre import inject_genre
