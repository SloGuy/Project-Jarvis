import json

from sqlalchemy import text

from app.market_db.database import engine


MIGRATION_STATEMENTS = (
    """
    ALTER TABLE market_news_article_assets
    ADD COLUMN IF NOT EXISTS link_type VARCHAR(30)
    NOT NULL DEFAULT 'legacy'
    """,
    """
    ALTER TABLE market_news_article_assets
    ADD COLUMN IF NOT EXISTS linked_by VARCHAR(30)
    NOT NULL DEFAULT 'legacy'
    """,
    """
    ALTER TABLE market_news_article_assets
    ADD COLUMN IF NOT EXISTS match_reason TEXT
    """,
    """
    ALTER TABLE market_news_article_assets
    ADD COLUMN IF NOT EXISTS matched_text TEXT
    """,
    """
    ALTER TABLE market_news_article_assets
    ADD COLUMN IF NOT EXISTS confidence_score NUMERIC(5, 4)
    NOT NULL DEFAULT 0.5000
    """,
)


def migrate_news_link_metadata() -> dict:
    completed = 0

    with engine.begin() as connection:
        for statement in MIGRATION_STATEMENTS:
            connection.execute(text(statement))
            completed += 1

    return {
        "status": "success",
        "migration": "news_link_metadata",
        "statements_completed": completed,
    }


if __name__ == "__main__":
    try:
        result = migrate_news_link_metadata()
    except Exception as error:
        result = {
            "status": "failed",
            "migration": "news_link_metadata",
            "error": str(error),
        }

    print(json.dumps(result, indent=2))
