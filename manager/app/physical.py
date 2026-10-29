"""Raster background images and manually positioned assets in physical views."""
from datetime import datetime
from io import BytesIO
import warnings
from typing import Annotated
from fastapi import APIRouter, HTTPException, Request, Query, Form, File, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, model_validator
from PIL import Image, UnidentifiedImageError
from psycopg.errors import ForeignKeyViolation, CheckViolation, DeadlockDetected, SerializationFailure
from starlette.concurrency import run_in_threadpool
from app.database import get_connection
from app.topology import ViewDetailsInput, topology_write

router = APIRouter(prefix="/api/physical", tags=["physical"])
MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_IMAGE_PIXELS = 40_000_000

class Position(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    id: int = Field(gt=0)
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)

class PositionsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(gt=0)
    positions: list[Position] = Field(max_length=100000)
    updated_at: datetime | None = None

    @model_validator(mode="after")
    def unique_positions(self):
        ids = [position.id for position in self.positions]
        if len(set(ids)) != len(ids):
            raise ValueError("An asset may have only one position")
        return self


def physical_view(connection, view_id, *, lock=False):
    view = connection.execute("""SELECT v.id,v.name,v.site_id,s.name AS site_name
        FROM topology_view v JOIN site s ON s.id=v.site_id
        WHERE v.id=%s AND v.type='physical'""" + (" FOR UPDATE OF v" if lock else ""), (view_id,)).fetchone()
    if view is None:
        raise HTTPException(404, "Physical view not found")
    return view


def validate_image(data):
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data), formats=("PNG", "JPEG")) as image:
                if image.format not in ('PNG','JPEG'):
                    raise HTTPException(422, "Upload a PNG or JPEG background image")
                width,height = image.size
                if not 1 <= width <= 16000 or not 1 <= height <= 16000 or width*height > MAX_IMAGE_PIXELS:
                    raise HTTPException(422, "Background image dimensions exceed the supported limit")
                media_type = 'image/png' if image.format == 'PNG' else 'image/jpeg'
                image.verify()
            with Image.open(BytesIO(data), formats=("PNG", "JPEG")) as image:
                image.load()
        return media_type,width,height
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise HTTPException(422, "Invalid or unsupported background image") from None


@router.get("")
def list_physical_views():
    with get_connection() as connection:
        return connection.execute("""SELECT v.id,v.name,v.site_id,v.created_at,s.name AS site_name,
            f.revision,f.updated_at,f.width,f.height,(f.topology_view_id IS NOT NULL) AS has_plan,
            (SELECT count(*) FROM topology_asset m WHERE m.topology_view_id=v.id) AS mapping_count,
            (SELECT count(*) FROM topology_position p WHERE p.topology_view_id=v.id) AS positioned_count
            FROM topology_view v JOIN site s ON s.id=v.site_id
            LEFT JOIN topology_floor_plan f ON f.topology_view_id=v.id
            WHERE v.type='physical' ORDER BY s.name,v.name,v.id""").fetchall()


def save_physical_view(view_id, value, data, metadata, revision):
    with topology_write(duplicate="A view with this name already exists in this Site", connection_factory=get_connection) as connection:
        if view_id is None:
            view = connection.execute("""INSERT INTO topology_view(site_id,name,type) VALUES(%s,%s,'physical')
                RETURNING id,site_id,name,type,created_at""", (value.site_id,value.name)).fetchone()
        else:
            physical_view(connection,view_id,lock=True)
            view = connection.execute("""UPDATE topology_view SET site_id=%s,name=%s WHERE id=%s AND type='physical'
                RETURNING id,site_id,name,type,created_at""", (value.site_id,value.name,view_id)).fetchone()
        if data is not None:
            write_background_image(connection,view['id'],data,metadata,revision)
    return view


async def physical_form(view_id, name, site_id, revision, image):
    value = ViewDetailsInput(name=name,site_id=site_id)
    data = metadata = None
    if image is not None:
        data = await image.read(MAX_IMAGE_BYTES+1)
        if len(data)>MAX_IMAGE_BYTES:
            raise HTTPException(413,"Background images must be at most 10 MB")
        metadata = await run_in_threadpool(validate_image,data)
    return await run_in_threadpool(save_physical_view,view_id,value,data,metadata,revision)


@router.post("", status_code=201)
async def create_physical_view(name: Annotated[str, Form(min_length=1,max_length=100,pattern=r".*\S.*")],
    site_id: Annotated[int, Form(gt=0)], revision: Annotated[int, Form(ge=0)] = 0,
    image: Annotated[UploadFile | None, File()] = None):
    return await physical_form(None,name,site_id,revision,image)


@router.put("/{view_id}")
async def update_physical_view(view_id: int, name: Annotated[str, Form(min_length=1,max_length=100,pattern=r".*\S.*")],
    site_id: Annotated[int, Form(gt=0)], revision: Annotated[int, Form(ge=0)] = 0,
    image: Annotated[UploadFile | None, File()] = None):
    return await physical_form(view_id,name,site_id,revision,image)


@router.delete("/{view_id}")
def delete_physical_view(view_id: int):
    with topology_write(duplicate="View already exists", connection_factory=get_connection) as connection:
        row=connection.execute("DELETE FROM topology_view WHERE id=%s AND type='physical' RETURNING id", (view_id,)).fetchone()
        if row is None:
            raise HTTPException(404,"Physical view not found")
    return {"id":row['id'],"deleted":True}


@router.get("/{view_id}")
def get_physical_view(view_id: int):
    with get_connection() as connection:
        # Keep the revision, plan metadata and positions in one consistent snapshot.
        connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        view = physical_view(connection, view_id)
        plan = connection.execute("SELECT width,height,revision,updated_at FROM topology_floor_plan WHERE topology_view_id=%s",(view_id,)).fetchone()
        nodes = connection.execute("""SELECT m.id,m.asset_id,m.parent_id,COALESCE(c.name,'â€”') AS type_name,
            host(a.ip) AS ip,a.display_name,a.discovered_hostname,a.operating_system_id,os.name AS operating_system_name,a.status,a.last_seen,a.latency_ms,
            pos.x,pos.y,
            COALESCE((SELECT jsonb_agg(jsonb_build_object('port',p.port,
                'protocol',CASE p.protocol WHEN 'T' THEN 'TCP' ELSE 'UDP' END,
                'state',CASE p.state WHEN 'O' THEN 'open' ELSE 'closed' END)
                ORDER BY p.port,p.protocol) FROM port p WHERE p.asset_id=a.id),'[]'::jsonb) AS ports
            FROM topology_asset m JOIN asset a ON a.id=m.asset_id
            LEFT JOIN asset_type c ON c.id=a.asset_type_id
            LEFT JOIN operating_system os ON os.id=a.operating_system_id
            LEFT JOIN topology_position pos ON pos.topology_asset_id=m.id
            WHERE m.topology_view_id=%s ORDER BY a.ip,m.id""",(view_id,)).fetchall()
    return {"view":view,"plan":plan,"nodes":nodes}


@router.get("/{view_id}/image")
def get_background_image(view_id: int, revision: Annotated[int | None, Query(gt=0)] = None):
    with get_connection() as connection:
        physical_view(connection, view_id)
        plan=connection.execute("SELECT image,media_type,revision FROM topology_floor_plan WHERE topology_view_id=%s",(view_id,)).fetchone()
    if plan is None:
        raise HTTPException(404,"Background image not found")
    if revision is not None and revision != plan['revision']:
        raise HTTPException(409,"Background image changed. Reopen the map.")
    return Response(bytes(plan['image']),media_type=plan['media_type'],headers={"Cache-Control":"no-store","X-Content-Type-Options":"nosniff"})


def write_background_image(connection, view_id, data, metadata, expected_revision):
    media_type,width,height=metadata
    existing=connection.execute("SELECT revision,updated_at FROM topology_floor_plan WHERE topology_view_id=%s FOR UPDATE",(view_id,)).fetchone()
    current=existing['revision'] if existing else 0
    if expected_revision != current:
        raise HTTPException(409,"Background image changed. Reload the list before uploading.")
    return connection.execute("""INSERT INTO topology_floor_plan(topology_view_id,image,media_type,width,height)
        VALUES(%s,%s,%s,%s,%s) ON CONFLICT(topology_view_id) DO UPDATE SET image=EXCLUDED.image,
        media_type=EXCLUDED.media_type,width=EXCLUDED.width,height=EXCLUDED.height,
        revision=topology_floor_plan.revision+1,updated_at=NOW()
        RETURNING topology_view_id,width,height,revision,updated_at""",(view_id,data,media_type,width,height)).fetchone()


def store_background_image(view_id, data, metadata, expected_revision):
    with get_connection(read_only=False) as connection:
        physical_view(connection,view_id,lock=True)
        return write_background_image(connection,view_id,data,metadata,expected_revision)


@router.put("/{view_id}/image")
async def upload_background_image(view_id: int, request: Request, revision: Annotated[int, Query(ge=0)]):
    data=bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data)>MAX_IMAGE_BYTES:
            raise HTTPException(413,"Background images must be at most 10 MB")
    if not data:
        raise HTTPException(422,"Choose a background image")
    metadata=await run_in_threadpool(validate_image,bytes(data))
    try:
        return await run_in_threadpool(store_background_image,view_id,bytes(data),metadata,revision)
    except (CheckViolation,ForeignKeyViolation):
        raise HTTPException(409,"Physical view changed. Reload and retry.") from None
    except (DeadlockDetected,SerializationFailure):
        raise HTTPException(409,"Topology changed concurrently. Reload and retry.") from None


@router.delete("/{view_id}/image")
def delete_background_image(view_id: int, revision: Annotated[int, Query(gt=0)], updated_at: Annotated[datetime, Query()]):
    with topology_write(duplicate="Background image already exists", connection_factory=get_connection) as connection:
        physical_view(connection,view_id,lock=True)
        plan=connection.execute("SELECT revision,updated_at FROM topology_floor_plan WHERE topology_view_id=%s FOR UPDATE",(view_id,)).fetchone()
        if plan is None:
            raise HTTPException(404,"Background image not found")
        if plan['revision']!=revision or plan['updated_at']!=updated_at:
            raise HTTPException(409,"The map changed. Reopen it before saving.")
        # Positions reference this image and are cleared by the existing FK cascade.
        # Asset associations and the named physical view remain intact.
        connection.execute("DELETE FROM topology_floor_plan WHERE topology_view_id=%s",(view_id,))
    return {"id":view_id,"deleted":True}


@router.put("/{view_id}/positions")
def save_positions(view_id: int, value: PositionsInput):
    try:
        with get_connection(read_only=False) as connection:
            physical_view(connection,view_id,lock=True)
            plan=connection.execute("SELECT revision,updated_at FROM topology_floor_plan WHERE topology_view_id=%s FOR UPDATE",(view_id,)).fetchone()
            if plan is None:
                raise HTTPException(409,"Upload a background image before positioning assets")
            if plan['revision']!=value.revision or (value.updated_at is not None and plan['updated_at']!=value.updated_at):
                raise HTTPException(409,"The map changed. Reopen it before saving.")
            ids=[position.id for position in value.positions]
            allowed=connection.execute("SELECT id FROM topology_asset WHERE topology_view_id=%s AND id=ANY(%s::bigint[])",(view_id,ids)).fetchall()
            if {row['id'] for row in allowed}!=set(ids):
                raise HTTPException(409,"Asset associations changed. Reopen the map.")
            connection.execute("DELETE FROM topology_position WHERE topology_view_id=%s",(view_id,))
            if value.positions:
                connection.cursor().executemany("INSERT INTO topology_position(topology_asset_id,topology_view_id,x,y) VALUES(%s,%s,%s,%s)",[(p.id,view_id,p.x,p.y) for p in value.positions])
            result=connection.execute("UPDATE topology_floor_plan SET revision=revision+1,updated_at=NOW() WHERE topology_view_id=%s RETURNING revision,updated_at",(view_id,)).fetchone()
        return result
    except (ForeignKeyViolation,CheckViolation):
        raise HTTPException(409,"Asset associations changed. Reopen the map.") from None
    except (DeadlockDetected,SerializationFailure):
        raise HTTPException(409,"Topology changed concurrently. Reload and retry.") from None
