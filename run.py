# run.py
from app import create_app, db
from app.models import User, Board, Post, Comment

app = create_app()

@app.shell_context_processor
def make_shell_context():
    return {'db': db, 'User': User, 'Board': Board, 'Post': Post, 'Comment': Comment}

def migrate_db():
    """为现有数据库添加新字段（SQLite 不支持自动迁移，手动 ALTER TABLE）"""
    from sqlalchemy import inspect, text
    inspector = inspect(db.engine)
    existing = {col['name'] for col in inspector.get_columns('user')}
    new_cols = {
        'bio': 'TEXT',
        'avatar_url': 'VARCHAR(256)',
        'custom_title': 'VARCHAR(64)',
        'search_per_page': 'INTEGER DEFAULT 20',
        'search_scope': "VARCHAR(20) DEFAULT 'all'",
        'search_type': "VARCHAR(20) DEFAULT 'all'",
    }
    with db.engine.connect() as conn:
        for col, col_type in new_cols.items():
            if col not in existing:
                conn.execute(text(f'ALTER TABLE "user" ADD COLUMN {col} {col_type}'))
        conn.commit()

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        migrate_db()
        if Board.query.count() == 0:
            boards = ['综合讨论', '观鸟记录', '东方鸟类考据', '绘画与创作']
            for name in boards:
                db.session.add(Board(name=name, description=f'{name}板块'))
            db.session.commit()
    app.run(debug=True)
