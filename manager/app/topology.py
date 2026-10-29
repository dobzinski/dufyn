"""Manual topology administration; discovery inventory remains independent."""
from contextlib import contextmanager
from typing import Annotated, Literal
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator
from psycopg.errors import CheckViolation, ForeignKeyViolation, RestrictViolation, UniqueViolation, SerializationFailure, DeadlockDetected
from app.database import get_connection

router = APIRouter(tags=["topology"])

class TypeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=100)

    @field_validator("name", mode="before")
    @classmethod
    def trim_name(cls, value):
        return value.strip() if isinstance(value, str) else value

class ViewDetailsInput(TypeInput):
    site_id: int = Field(gt=0)

class ViewInput(ViewDetailsInput):
    type: Literal["physical", "logical"]

class MappingInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    topology_view_id: int = Field(gt=0)
    asset_id: int = Field(gt=0)
    parent_id: int | None = Field(default=None, gt=0)


class SiteLinkInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_id: int = Field(gt=0)
    destination_id: int = Field(gt=0)


@contextmanager
def topology_write(*, duplicate, connection_factory=None,
          linked="Check linked records: assets must belong to the view Site and parents to the same view. Types in use cannot be deleted.",
          invalid="Invalid topology values: an item cannot be its own parent or form a cycle."):
    try:
        with (connection_factory or get_connection)(read_only=False) as connection:
            yield connection
    except UniqueViolation:
        raise HTTPException(409, duplicate) from None
    except (ForeignKeyViolation, RestrictViolation):
        raise HTTPException(409, linked) from None
    except CheckViolation as error:
        guards = {
            "Remove interconnections before moving this mapping to another view": "Remove site links before moving this association to another view",
            "Remove interconnections before changing this view type or Site": "Remove site links before changing this view type or Site",
        }
        detail = error.diag.message_primary
        raise HTTPException(422, guards.get(detail, invalid)) from None
    except (SerializationFailure, DeadlockDetected):
        raise HTTPException(409, "Topology changed concurrently. Reload and retry.") from None

def write(statement, params, *, missing, duplicate, **errors):
    with topology_write(duplicate=duplicate, **errors) as connection:
        row = connection.execute(statement, params).fetchone()
        if row is None:
            raise HTTPException(404, missing)
    return row

@router.get("/api/types")
def list_types():
    with get_connection() as connection:
        return connection.execute("SELECT t.id, t.name, (SELECT count(*) FROM asset a WHERE a.asset_type_id=t.id) AS asset_count FROM asset_type t ORDER BY t.name,t.id").fetchall()

@router.post("/api/types", status_code=201)
def create_type(value: TypeInput):
    return write("INSERT INTO asset_type(name) VALUES (%s) RETURNING id,name", (value.name,), missing="Type not found", duplicate="A type with this name already exists")

@router.put("/api/types/{record_id}")
def update_type(record_id: int, value: TypeInput):
    return write("UPDATE asset_type SET name=%s WHERE id=%s RETURNING id,name", (value.name,record_id), missing="Type not found", duplicate="A type with this name already exists")

@router.delete("/api/types/{record_id}")
def delete_type(record_id: int):
    row=write("DELETE FROM asset_type WHERE id=%s RETURNING id", (record_id,), missing="Type not found", duplicate="Type already exists")
    return {"id":row["id"], "deleted":True}

@router.get("/api/views")
def list_views():
    with get_connection() as connection:
        return connection.execute("""SELECT v.id,v.site_id,v.name,v.type,v.created_at,s.name AS site_name,
            (SELECT count(*) FROM topology_asset m WHERE m.topology_view_id=v.id) AS mapping_count
            FROM topology_view v JOIN site s ON s.id=v.site_id ORDER BY s.name,v.name,v.id""").fetchall()

@router.post("/api/views", status_code=201)
def create_view(value: ViewInput):
    return write("INSERT INTO topology_view(site_id,name,type) VALUES (%s,%s,%s) RETURNING id,site_id,name,type,created_at", (value.site_id,value.name,value.type), missing="View not found", duplicate="A view with this name and type already exists in this Site")

@router.put("/api/views/{record_id}")
def update_view(record_id: int, value: ViewInput):
    return write("UPDATE topology_view SET site_id=%s,name=%s,type=%s WHERE id=%s RETURNING id,site_id,name,type,created_at", (value.site_id,value.name,value.type,record_id), missing="View not found", duplicate="A view with this name and type already exists in this Site")

@router.delete("/api/views/{record_id}")
def delete_view(record_id: int):
    row=write("DELETE FROM topology_view WHERE id=%s RETURNING id", (record_id,), missing="View not found", duplicate="View already exists")
    return {"id":row["id"], "deleted":True}

@router.get("/api/mapping/assets")
def mapping_asset_options(site_id: Annotated[int | None, Query(gt=0)] = None):
    with get_connection() as connection:
        return connection.execute("""SELECT id,site_id,host(ip) AS ip,
            concat_ws(' - ',host(ip),COALESCE(NULLIF(display_name,''),NULLIF(discovered_hostname,''))) AS name
            FROM asset a""" + (" WHERE a.site_id=%s" if site_id is not None else "") + " ORDER BY a.ip,a.id",
            (site_id,) if site_id is not None else ()).fetchall()

@router.get("/api/mapping")
def list_mapping(topology_view_id: Annotated[int | None, Query(gt=0)] = None):
    with get_connection() as connection:
        return connection.execute("""SELECT m.id,m.topology_view_id,m.asset_id,m.parent_id,m.site_id,m.created_at,
            v.name AS view_name,v.type AS view_type,s.name AS site_name,COALESCE(c.name,'—') AS type_name,
            host(a.ip) AS ip, a.display_name,a.discovered_hostname,
            concat_ws(' - ',host(a.ip),COALESCE(NULLIF(a.display_name,''),NULLIF(a.discovered_hostname,''))) AS name,
            CASE WHEN p.id IS NULL THEN NULL ELSE
                concat_ws(' - ',host(pa.ip),COALESCE(NULLIF(pa.display_name,''),NULLIF(pa.discovered_hostname,''))) END AS parent_name
            FROM topology_asset m JOIN topology_view v ON v.id=m.topology_view_id
            JOIN site s ON s.id=m.site_id JOIN asset a ON a.id=m.asset_id
            LEFT JOIN asset_type c ON c.id=a.asset_type_id
            LEFT JOIN topology_asset p ON p.id=m.parent_id LEFT JOIN asset pa ON pa.id=p.asset_id
            """ + (" WHERE m.topology_view_id=%s" if topology_view_id is not None else "") + " ORDER BY s.name,v.name,a.ip,m.id",
            (topology_view_id,) if topology_view_id is not None else ()).fetchall()

@router.post("/api/mapping", status_code=201)
def create_mapping(value: MappingInput):
    return write("""INSERT INTO topology_asset(topology_view_id,asset_id,parent_id)
        VALUES (%s,%s,%s) RETURNING id,topology_view_id,asset_id,parent_id,site_id,created_at""",
        (value.topology_view_id,value.asset_id,value.parent_id), missing="Association not found", duplicate="This asset is already associated with this view")

@router.put("/api/mapping/{record_id}")
def update_mapping(record_id: int, value: MappingInput):
    return write("""UPDATE topology_asset SET topology_view_id=%s,asset_id=%s,parent_id=%s
        WHERE id=%s RETURNING id,topology_view_id,asset_id,parent_id,site_id,created_at""",
        (value.topology_view_id,value.asset_id,value.parent_id,record_id), missing="Association not found", duplicate="This asset is already associated with this view")

@router.delete("/api/mapping/{record_id}")
def delete_mapping(record_id: int):
    row=write("DELETE FROM topology_asset WHERE id=%s RETURNING id", (record_id,), missing="Association not found", duplicate="Association already exists")
    return {"id":row["id"], "deleted":True}


@router.get("/api/views/{view_id}/mappings")
def view_mappings(view_id: int):
    return list_mapping(view_id)


def check_mapping_owner(view_id, value):
    if value.topology_view_id != view_id:
        raise HTTPException(422, "Check the field values.")


@router.post("/api/views/{view_id}/mappings", status_code=201)
def create_view_mapping(view_id: int, value: MappingInput):
    check_mapping_owner(view_id, value)
    return create_mapping(value)


@router.put("/api/views/{view_id}/mappings/{record_id}")
def update_view_mapping(view_id: int, record_id: int, value: MappingInput):
    check_mapping_owner(view_id, value)
    return write("""UPDATE topology_asset SET asset_id=%s,parent_id=%s
        WHERE id=%s AND topology_view_id=%s RETURNING id,topology_view_id,asset_id,parent_id,site_id,created_at""",
        (value.asset_id,value.parent_id,record_id,view_id), missing="Association not found", duplicate="This asset is already associated with this view")


@router.delete("/api/views/{view_id}/mappings/{record_id}")
def delete_view_mapping(view_id: int, record_id: int):
    row=write("DELETE FROM topology_asset WHERE id=%s AND topology_view_id=%s RETURNING id", (record_id,view_id),
        missing="Association not found", duplicate="Association already exists")
    return {"id":row["id"], "deleted":True}


@router.post("/api/logical", status_code=201)
def create_logical_view(value: ViewDetailsInput):
    return create_view(ViewInput(**value.model_dump(), type="logical"))


@router.put("/api/logical/{view_id}")
def update_logical_view(view_id: int, value: ViewDetailsInput):
    return write("""UPDATE topology_view SET site_id=%s,name=%s WHERE id=%s AND type='logical'
        RETURNING id,site_id,name,type,created_at""", (value.site_id,value.name,view_id),
        missing="Logical view not found", duplicate="A view with this name and type already exists in this Site")


@router.delete("/api/logical/{view_id}")
def delete_logical_view(view_id: int):
    row=write("DELETE FROM topology_view WHERE id=%s AND type='logical' RETURNING id", (view_id,),
        missing="Logical view not found", duplicate="View already exists")
    return {"id":row["id"], "deleted":True}


@router.get("/api/logical")
def list_logical_views():
    with get_connection() as connection:
        return connection.execute("""SELECT v.id,v.name,v.site_id,v.created_at,s.name AS site_name,
            (SELECT count(*) FROM topology_asset m WHERE m.topology_view_id=v.id) AS mapping_count
            FROM topology_view v JOIN site s ON s.id=v.site_id
            WHERE v.type='logical' ORDER BY s.name,v.name,v.id""").fetchall()


def logical_nodes(connection, view_ids):
    return connection.execute("""SELECT m.id,m.topology_view_id,m.asset_id,m.parent_id,COALESCE(c.name,'—') AS type_name,
            host(a.ip) AS ip,a.display_name,a.discovered_hostname,a.operating_system_id,os.name AS operating_system_name,a.status,a.last_seen,a.latency_ms,
            COALESCE((SELECT jsonb_agg(jsonb_build_object(
                'port',p.port,'protocol',CASE p.protocol WHEN 'T' THEN 'TCP' ELSE 'UDP' END,
                'state',CASE p.state WHEN 'O' THEN 'open' ELSE 'closed' END)
                ORDER BY p.port,p.protocol) FROM port p WHERE p.asset_id=a.id), '[]'::jsonb) AS ports
            FROM topology_asset m JOIN asset a ON a.id=m.asset_id
            LEFT JOIN asset_type c ON c.id=a.asset_type_id
            LEFT JOIN operating_system os ON os.id=a.operating_system_id
            WHERE m.topology_view_id=ANY(%s::bigint[]) ORDER BY a.ip,m.id""", (view_ids,)).fetchall()


@router.get("/api/logical/{view_id}")
def logical_view(view_id: int):
    with get_connection() as connection:
        view = connection.execute("""SELECT v.id,v.name,v.site_id,s.name AS site_name
            FROM topology_view v JOIN site s ON s.id=v.site_id
            WHERE v.id=%s AND v.type='logical'""", (view_id,)).fetchone()
        if view is None:
            raise HTTPException(404, "Logical view not found")
        nodes = logical_nodes(connection, [view_id])
    return {"view":view,"nodes":nodes}


@router.get("/api/links/mappings")
def sitelink_mapping_options():
    with get_connection() as connection:
        return connection.execute("""SELECT m.id,m.topology_view_id,m.site_id,
            concat_ws(' - ',host(a.ip),COALESCE(NULLIF(a.display_name,''),NULLIF(a.discovered_hostname,''))) AS name
            FROM topology_asset m JOIN topology_view v ON v.id=m.topology_view_id
            JOIN asset a ON a.id=m.asset_id WHERE v.type='logical'
            ORDER BY v.site_id,v.name,a.ip,m.id""").fetchall()


def sitelink(connection, record_id):
    row = connection.execute("SELECT id,name,created_at FROM topology_interconnection WHERE id=%s", (record_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "Site link not found")
    return row


@router.get("/api/links")
def list_links():
    with get_connection() as connection:
        return connection.execute("""SELECT g.id,g.name,g.created_at,
            (SELECT count(*) FROM topology_logical_link l WHERE l.interconnection_id=g.id) AS association_count,
            (SELECT count(DISTINCT m.topology_view_id) FROM topology_logical_link l
                JOIN topology_asset m ON m.id=l.source_id OR m.id=l.destination_id
                WHERE l.interconnection_id=g.id) AS view_count
            FROM topology_interconnection g ORDER BY g.name,g.id""").fetchall()


@router.post("/api/links", status_code=201)
def create_sitelink(value: TypeInput):
    return write("INSERT INTO topology_interconnection(name) VALUES (%s) RETURNING id,name,created_at",
        (value.name,), missing="Site link not found", duplicate="A site link with this name already exists")


@router.put("/api/links/{record_id}")
def update_sitelink(record_id: int, value: TypeInput):
    return write("UPDATE topology_interconnection SET name=%s WHERE id=%s RETURNING id,name,created_at",
        (value.name,record_id), missing="Site link not found", duplicate="A site link with this name already exists")


@router.delete("/api/links/{record_id}")
def delete_sitelink(record_id: int):
    row = write("DELETE FROM topology_interconnection WHERE id=%s RETURNING id", (record_id,),
        missing="Site link not found", duplicate="Site link already exists")
    return {"id":row["id"], "deleted":True}


@router.get("/api/links/{record_id}/links")
def list_sitelink_links(record_id: int):
    with get_connection() as connection:
        sitelink(connection, record_id)
        return connection.execute("""SELECT l.id,l.source_id,l.destination_id,l.created_at,
            sv.id AS source_view_id,dv.id AS destination_view_id,
            ss.name AS source_site_name,ds.name AS destination_site_name,
            sv.name AS source_view_name,dv.name AS destination_view_name,
            concat_ws(' - ',host(sa.ip),COALESCE(NULLIF(sa.display_name,''),NULLIF(sa.discovered_hostname,''))) AS source_name,
            concat_ws(' - ',host(da.ip),COALESCE(NULLIF(da.display_name,''),NULLIF(da.discovered_hostname,''))) AS destination_name
            FROM topology_logical_link l
            JOIN topology_asset sm ON sm.id=l.source_id JOIN topology_asset dm ON dm.id=l.destination_id
            JOIN topology_view sv ON sv.id=sm.topology_view_id JOIN topology_view dv ON dv.id=dm.topology_view_id
            JOIN site ss ON ss.id=sv.site_id JOIN site ds ON ds.id=dv.site_id
            JOIN asset sa ON sa.id=sm.asset_id JOIN asset da ON da.id=dm.asset_id
            WHERE l.interconnection_id=%s
            ORDER BY ss.name,sv.name,sa.ip,ds.name,dv.name,da.ip,l.id""", (record_id,)).fetchall()


def write_sitelink_link(statement, params):
    return write(statement, params, missing="Association or site link not found",
        duplicate="These assets are already linked in this diagram",
        linked="Association or site link no longer exists. Reload the list.",
        invalid="Select assets associated with logical views in different Sites.")


@router.post("/api/links/{record_id}/links", status_code=201)
def create_sitelink_link(record_id: int, value: SiteLinkInput):
    return write_sitelink_link("""INSERT INTO topology_logical_link(interconnection_id,source_id,destination_id)
        SELECT id,%s,%s FROM topology_interconnection WHERE id=%s
        RETURNING id,interconnection_id AS sitelink_id,source_id,destination_id,created_at""",
        (value.source_id,value.destination_id,record_id))


@router.put("/api/links/{record_id}/links/{link_id}")
def update_sitelink_link(record_id: int, link_id: int, value: SiteLinkInput):
    return write_sitelink_link("""UPDATE topology_logical_link SET source_id=%s,destination_id=%s
        WHERE id=%s AND interconnection_id=%s RETURNING id,interconnection_id AS sitelink_id,source_id,destination_id,created_at""",
        (value.source_id,value.destination_id,link_id,record_id))


@router.delete("/api/links/{record_id}/links/{link_id}")
def delete_sitelink_link(record_id: int, link_id: int):
    row = write_sitelink_link("DELETE FROM topology_logical_link WHERE id=%s AND interconnection_id=%s RETURNING id",
        (link_id,record_id))
    return {"id":row["id"], "deleted":True}


@router.get("/api/links/{record_id}/topology")
def sitelink_topology(record_id: int):
    with get_connection() as connection:
        connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        diagram = sitelink(connection, record_id)
        links = connection.execute("SELECT id,source_id,destination_id FROM topology_logical_link WHERE interconnection_id=%s ORDER BY id",
            (record_id,)).fetchall()
        views = connection.execute("""SELECT DISTINCT v.id,v.name,v.site_id,s.name AS site_name
            FROM topology_logical_link l JOIN topology_asset m ON m.id=l.source_id OR m.id=l.destination_id
            JOIN topology_view v ON v.id=m.topology_view_id JOIN site s ON s.id=v.site_id
            WHERE l.interconnection_id=%s ORDER BY s.name,v.name,v.id""", (record_id,)).fetchall()
        nodes = logical_nodes(connection, [view["id"] for view in views]) if views else []
    return {"view":diagram,"views":views,"nodes":nodes,"links":links}
