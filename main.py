import os
import sqlite3
import json
from datetime import datetime
from dotenv import load_dotenv
import os
import re
from markdown_it import MarkdownIt
from notion_client import Client
from notion_to_md import NotionToMarkdown
from bs4 import BeautifulSoup


from flask import (
    Flask,
    g,
    render_template,
    render_template_string,
    request,
    redirect,
    url_for,
    abort,
    flash,
)

load_dotenv() # Loads variables from .env file
notion_key = os.getenv("NOTION_KEY")
flask_key = os.getenv("FLASK_KEY")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# Use env var if provided (e.g. FLASK_SQLITE_PATH=/data/db.sqlite3), otherwise default to project file
DB_PATH = os.getenv("FLASK_SQLITE_PATH", os.path.join(BASE_DIR, "data.db"))
DB_DIR = os.path.dirname(DB_PATH)


def create_app():
    app = Flask(__name__)
    # NOTE: in production, replace with a secure secret key
    app.secret_key = os.environ.get(flask_key, "dev-secret")

    # Some Flask environments may not expose `before_first_request` the same way;
    # initialize the DB during app creation so the table exists before use.

    def slugify(value: str) -> str:
        """Simple slugifier: lowercase, replace non-alnum with hyphens, collapse hyphens."""
        value = (value or "").strip().lower()
        # replace non-alphanumeric with hyphen
        value = re.sub(r"[^a-z0-9]+", "-", value)
        # collapse multiple hyphens
        value = re.sub(r"-{2,}", "-", value).strip("-")
        return value or "item"

    def generate_unique_slug(db_conn, base_slug: str) -> str:
        """Ensure the slug is unique in the items table by appending -1, -2, ... as needed."""
        cur = db_conn.cursor()
        slug = base_slug
        idx = 1
        cur.execute("SELECT COUNT(1) FROM items WHERE slug = ?", (slug,))
        exists = cur.fetchone()[0] > 0
        while exists:
            slug = f"{base_slug}-{idx}"
            idx += 1
            cur.execute("SELECT COUNT(1) FROM items WHERE slug = ?", (slug,))
            exists = cur.fetchone()[0] > 0
        return slug

    def get_db():
        if "db" not in g:
            conn = sqlite3.connect(DB_PATH, detect_types=sqlite3.PARSE_DECLTYPES)
            conn.row_factory = sqlite3.Row
            g.db = conn
        return g.db

    @app.teardown_appcontext
    def close_db(exception=None):
        db = g.pop("db", None)
        if db is not None:
            db.close()

    def init_db():
        # Ensure the directory for the DB file exists (useful when mounted as a volume at /data)
        os.makedirs(DB_DIR or BASE_DIR, exist_ok=True)
        conn = sqlite3.connect(DB_PATH, detect_types=sqlite3.PARSE_DECLTYPES)
        cur = conn.cursor()
        # Create table (if not exists) with slug and subject columns
        cur.execute(
            """
        CREATE TABLE IF NOT EXISTS items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            date TEXT NOT NULL,
            is_index BOOLEAN,
            author TEXT DEFAULT 'Jamie Z',
            slug TEXT UNIQUE,
            subject TEXT DEFAULT ''
        )
        """
        )
        conn.commit()

        # Populate missing slugs for existing rows
        cur.execute("SELECT id, title, slug FROM items WHERE slug IS NULL OR slug = ''")
        rows = cur.fetchall()
        for row in rows:
            item_id = row[0]
            title = row[1] or "item"
            base = slugify(title)
            unique = generate_unique_slug(conn, base)
            cur.execute("UPDATE items SET slug = ? WHERE id = ?", (unique, item_id))
        conn.commit()
        conn.close()

    @app.route("/", methods=["GET"])
    def index():
        return render_template("form.html")

    @app.route("/submit", methods=["POST"])
    def submit():
        # The form should send a field called `param` (e.g., a name)
        page_id = request.form.get("param", "")
        indexed = request.form.get("index", "")
        author = request.form.get("author", "")
        subject = request.form.get("subject", "")  # <-- new field
        if author == "":
            author = "Jamie Z"
        if not page_id:
            flash("Please provide a value.")
            return redirect(url_for("index"))
        notion = Client(auth=notion_key)
        try:
            # Fetch the page object
            page = notion.pages.retrieve(page_id=page_id)

            # Get created time
            created_time = page.get("created_time", "No creation date found")
            title = page["properties"]["title"]["title"][0]["text"]["content"]
            print(title)

        except Exception as e:
            print(f"An error occurred: {e}")
            flash("An error occurred while fetching the Notion page. Please check the page ID and try again.")
            return redirect(url_for("index"))

        created_time = page.get("created_time", "No creation date found")


        n2m = NotionToMarkdown(notion)

        # Export a page as a markdown blocks
        md_blocks = n2m.page_to_markdown(page_id)

        # Convert markdown blocks to string
        md_str = n2m.to_markdown_string(md_blocks).get('parent')
        md = MarkdownIt()
        html_output = md.render(md_str)
        html_output = parse_html(html_output, title, author, created_time)


        db = get_db()
        cur = db.cursor()

        # generate slug and ensure uniqueness
        base_slug = slugify(title)
        slug = generate_unique_slug(db, base_slug)

        cur.execute(
            "INSERT INTO items (title, content, date, is_index, author, slug, subject) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (title, html_output, created_time, indexed, author, slug, subject),
        )
        db.commit()
        item_id = cur.lastrowid

        # redirect using slug instead of numeric id
        return redirect(url_for("view_item", slug=slug))

    @app.route("/items", methods=["GET"])
    def list_items():
        db = get_db()
        cur = db.cursor()
        # include subject so templates can display or filter by it
        cur.execute("SELECT id, title, content, slug, subject FROM items ORDER BY date DESC")
        items = cur.fetchall()
        return render_template("list.html", items=items)
    
    @app.route("/get", methods=["GET"])
    def fetch_pages():
        db = get_db()
        cur = db.cursor()

        # Read query params
        subject = request.args.get("subject")
        start = request.args.get("start")  # inclusive start date (ISO string preferred)
        end = request.args.get("end")      # inclusive end date (ISO string preferred)
        order_by = request.args.get("order_by", "date").lower()
        order = request.args.get("order", "desc").lower()
        limit = request.args.get("limit")
        offset = request.args.get("offset")

        # Whitelist columns to avoid SQL injection via order_by
        allowed_order_cols = {"date", "title", "subject"}
        if order_by not in allowed_order_cols:
            order_by = "date"

        # Normalize order direction
        order_dir = "ASC" if order == "asc" else "DESC"

        # Build base query and parameters list
        sql = "SELECT title, slug, date, subject FROM items"
        where_clauses = []
        params = []

        if subject:
            where_clauses.append("subject = ?")
            params.append(subject)

        # Compare date strings lexicographically (ISO8601 is safe for this)
        if start:
            where_clauses.append("date >= ?")
            params.append(start)
        if end:
            where_clauses.append("date <= ?")
            params.append(end)

        if where_clauses:
            sql += " WHERE " + " AND ".join(where_clauses)

        sql += f" ORDER BY {order_by} {order_dir}"

        # Optional limit/offset (only accept integers)
        if limit and limit.isdigit():
            sql += " LIMIT ?"
            params.append(int(limit))
            if offset and offset.isdigit():
                sql += " OFFSET ?"
                params.append(int(offset))

        cur.execute(sql, params)
        items = cur.fetchall()
        return json.dumps([dict(item) for item in items])

    @app.route("/get/<slug>", methods=["GET"])
    def view_item(slug):
        db = get_db()
        cur = db.cursor()
        cur.execute("SELECT * FROM items WHERE slug = ?", (slug,))
        row = cur.fetchone()
        if not row:
            abort(404)

        return render_template_string(row["content"], item=row)

    # expose helper for ad-hoc CLI usage
    app.init_db = init_db
    app.get_db = get_db

    # Initialize DB here to avoid relying on `before_first_request` behavior.
    init_db()

    return app
def parse_html(html_content: str, title, author, created_time) -> str:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html_content, "html.parser")

    # Tags we should NOT touch (code blocks, scripts, styles, etc.)
    skip_tags = {"code", "pre", "script", "style", "math", "svg", "textarea"}

    # Iterate over text nodes and replace math delimiters with LaTeX delimiters
    for text_node in soup.find_all(string=True):
        parent_name = text_node.parent.name if text_node.parent else None
        if parent_name in skip_tags:
            continue

        text = str(text_node)
        # Prefer handling display math ($$...$$) first
        if "$$" in text:
            parts = text.split("$$")
            # Rebuild with \[ ... \] for odd parts
            new_frag = ""
            for i, part in enumerate(parts):
                if i % 2 == 1:  # inside $$...$$
                    new_frag += f"\\[{part}\\]"
                else:
                    new_frag += part
            replacement = BeautifulSoup(new_frag, "html.parser")
            text_node.replace_with(replacement)

        # Then inline math ($...$)
        elif "$" in text:
            parts = text.split("$")
            new_frag = ""
            for i, part in enumerate(parts):
                if i % 2 == 1:  # inside $...$
                    new_frag += f"\\({part}\\)"
                else:
                    new_frag += part
            replacement = BeautifulSoup(new_frag, "html.parser")
            text_node.replace_with(replacement)

    # Build title and author/date tags
    title_tag = soup.new_tag("h1")
    title_tag.string = title
    title_tag["class"] = "heading-primary"

    author_tag = soup.new_tag("i")
    try:
        date_str = (
            created_time.split("T")[0]
            if isinstance(created_time, str) and "T" in created_time
            else str(created_time)
        )
    except Exception:
        date_str = str(created_time)
    author_tag.string = f"{author} {date_str}"

    # Add MathJax script (v3) with id and async
    mathjax_script = soup.new_tag(
        "script",
        id="MathJax-script",
        src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-mml-chtml.js",
    )
    # ensure async attribute present
    mathjax_script.attrs["async"] = "async"

    # Prepend script, title, and author so they appear before content
    soup.insert(0, mathjax_script)
    soup.insert(0, author_tag)
    soup.insert(0, title_tag)

    return str(soup)
    
    
    
app = create_app()

if __name__ == "__main__":
    # initialize DB and run development server
    
    app.init_db()
    debug = os.getenv("FLASK_DEBUG", "0") == "1"
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)), debug=debug, use_reloader=False)
