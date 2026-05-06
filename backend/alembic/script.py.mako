"""${message}"""

from alembic import op
import sqlalchemy as sa
${imports if imports else ""}

${upgrades if upgrades else "pass"}


${downgrades if downgrades else "pass"}
