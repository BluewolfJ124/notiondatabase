# Notion Database Flask Application

This Flask application serves Notion pages via a RESTful API. It allows you to fetch and display content from your Notion databases.

## Features

- Connects to Notion API to retrieve pages and databases
- Serves Notion content as JSON or rendered HTML
- Simple REST endpoints for integration

## Requirements

- Python 3.8+
- Flask
- `notion-client` (or your preferred Notion API library)
- Notion integration token

## Installation

```bash
git clone https://github.com/yourusername/notiondatabase.git
cd notiondatabase
pip install -r requirements.txt
```

## Configuration

1. Create a `.env` file with your Notion integration token:
    ```
    NOTION_TOKEN=your_secret_token
    ```

2. (Optional) Add your database/page IDs as environment variables or in a config file.

## Usage

```bash
flask run
```

Visit `http://localhost:5000/` to access the API.

## Example Endpoints

- `/pages/<page_id>`: Get a Notion page by ID
- `/databases/<database_id>`: Get a Notion database by ID

## License

MIT

## Acknowledgements

- [Flask](https://flask.palletsprojects.com/)
- [Notion API](https://developers.notion.com/)
