"""Operating system catalog, following the existing Types CRUD pattern."""
from fastapi import APIRouter

from app.database import get_connection
from app.topology import TypeInput, write


router = APIRouter(prefix="/api/os", tags=["operating systems"])


@router.get("")
def list_operating_systems():
    with get_connection() as connection:
        return connection.execute("""SELECT o.id, o.name,
            (SELECT count(*) FROM asset a WHERE a.operating_system_id=o.id) AS asset_count
            FROM operating_system o ORDER BY (o.name='Other'), o.name, o.id""").fetchall()


@router.get("/top-assets")
def top_operating_systems():
    with get_connection() as connection:
        return connection.execute("""
            SELECT o.id, o.name, count(*) AS total
            FROM asset a JOIN operating_system o ON o.id = a.operating_system_id
            GROUP BY o.id, o.name
            ORDER BY total DESC, o.name ASC, o.id ASC
            LIMIT 5
        """).fetchall()


def write_operating_system(statement, params):
    return write(statement, params, connection_factory=get_connection,
        missing="Operating system not found",
        duplicate="An operating system with this name already exists",
        linked="Operating systems in use by assets cannot be deleted.")


@router.post("", status_code=201)
def create_operating_system(value: TypeInput):
    return write_operating_system("INSERT INTO operating_system(name) VALUES (%s) RETURNING id,name", (value.name,))


@router.put("/{record_id}")
def update_operating_system(record_id: int, value: TypeInput):
    return write_operating_system("UPDATE operating_system SET name=%s WHERE id=%s RETURNING id,name", (value.name, record_id))


@router.delete("/{record_id}")
def delete_operating_system(record_id: int):
    row = write_operating_system("DELETE FROM operating_system WHERE id=%s RETURNING id", (record_id,))
    return {"id": row["id"], "deleted": True}
