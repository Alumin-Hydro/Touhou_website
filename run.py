# run.py
from app import create_app, db
from app.models import User, Board, Post, Comment
from app.maintenance import initialize_database, migrate_schema

app = create_app()

@app.shell_context_processor
def make_shell_context():
    return {'db': db, 'User': User, 'Board': Board, 'Post': Post, 'Comment': Comment}

def migrate_db():
    """兼容旧部署命令；新代码统一由 app.maintenance 维护。"""
    migrate_schema()

if __name__ == '__main__':
    with app.app_context():
        initialize_database()
    app.run(debug=True)
