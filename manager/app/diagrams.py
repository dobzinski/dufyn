"""Named multi-Site diagram diagrams built from inventory Assets and Targets."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from app.database import get_connection
from app.topology import TypeInput, write

router = APIRouter(prefix="/api/diagrams", tags=["diagrams"])


class MemberInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    asset_id: int | None = Field(default=None, gt=0)
    target_id: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def one_inventory_record(self):
        if (self.asset_id is None) == (self.target_id is None):
            raise ValueError("Select exactly one Asset or Target")
        return self


class RelationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_asset_id: int | None = Field(default=None, gt=0)
    source_target_id: int | None = Field(default=None, gt=0)
    destination_asset_id: int | None = Field(default=None, gt=0)
    destination_target_id: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def valid_endpoints(self):
        for side in ["source", "destination"]:
            if (getattr(self,side+"_asset_id") is None) == (getattr(self,side+"_target_id") is None):
                raise ValueError("Select exactly one Asset or Target for each endpoint")
        for kind in ["asset", "target"]:
            value = getattr(self,"source_"+kind+"_id")
            if value is not None and value == getattr(self,"destination_"+kind+"_id"):
                raise ValueError("An item cannot connect to itself")
        return self


def diagram_write(statement, params, *, missing="Diagram or association not found", duplicate="This association already exists"):
    return write(statement,params,missing=missing,duplicate=duplicate,
        linked="Select resources included in this diagram. Reload the list if an item was removed.",
        invalid="Select one Asset or Target per endpoint; an item cannot connect to itself.")


def diagram_record(connection, diagram_id):
    row = connection.execute("SELECT id,name,created_at FROM topology_diagram WHERE id=%s", (diagram_id,)).fetchone()
    if row is None:
        raise HTTPException(404,"Diagram not found")
    return row


MEMBER_COLUMNS = """m.id,m.diagram_id,m.asset_id,m.target_id,m.created_at,
    CASE WHEN m.asset_id IS NOT NULL THEN 'asset' ELSE 'target' END AS kind,
    COALESCE(a.site_id,t.site_id) AS site_id,s.name AS site_name,
    CASE WHEN m.asset_id IS NOT NULL THEN concat_ws(' - ',host(a.ip),COALESCE(NULLIF(a.display_name,''),NULLIF(a.discovered_hostname,''))) ELSE t.fqdn END AS name,
    COALESCE(host(a.ip),t.fqdn) AS ip,t.fqdn,a.display_name,a.discovered_hostname,
    a.operating_system_id,os.name AS operating_system_name,
    CASE WHEN m.asset_id IS NOT NULL THEN COALESCE(c.name,'Asset') ELSE 'Target' END AS type_name,
    COALESCE(a.status,t.status) AS status,COALESCE(a.last_seen,t.last_seen) AS last_seen,
    COALESCE(a.latency_ms,t.latency_ms) AS latency_ms"""
MEMBER_JOINS = """FROM topology_diagram_member m LEFT JOIN asset a ON a.id=m.asset_id
    LEFT JOIN target t ON t.id=m.target_id JOIN site s ON s.id=COALESCE(a.site_id,t.site_id)
    LEFT JOIN asset_type c ON c.id=a.asset_type_id
    LEFT JOIN operating_system os ON os.id=a.operating_system_id"""


def members(connection, diagram_id, *, ports=False):
    port_column = """, COALESCE((SELECT jsonb_agg(jsonb_build_object('port',p.port,
        'protocol',CASE p.protocol WHEN 'T' THEN 'TCP' ELSE 'UDP' END,
        'state',CASE p.state WHEN 'O' THEN 'open' ELSE 'closed' END) ORDER BY p.port,p.protocol)
        FROM port p WHERE p.asset_id=m.asset_id OR p.target_id=m.target_id),'[]'::jsonb) AS ports""" if ports else ""
    return connection.execute("SELECT "+MEMBER_COLUMNS+port_column+" "+MEMBER_JOINS+
        " WHERE m.diagram_id=%s ORDER BY s.name,name,m.id", (diagram_id,)).fetchall()


def relations(connection, diagram_id):
    return connection.execute("""SELECT r.id,r.diagram_id,r.source_asset_id,r.source_target_id,
        r.destination_asset_id,r.destination_target_id,r.created_at,
        sm.id AS source_id,dm.id AS destination_id,
        ss.name AS source_site_name,ds.name AS destination_site_name,
        CASE WHEN sm.asset_id IS NOT NULL THEN 'Asset' ELSE 'Target' END AS source_kind,
        CASE WHEN dm.asset_id IS NOT NULL THEN 'Asset' ELSE 'Target' END AS destination_kind,
        CASE WHEN sm.asset_id IS NOT NULL THEN concat_ws(' - ',host(sa.ip),COALESCE(NULLIF(sa.display_name,''),NULLIF(sa.discovered_hostname,''))) ELSE st.fqdn END AS source_name,
        CASE WHEN dm.asset_id IS NOT NULL THEN concat_ws(' - ',host(da.ip),COALESCE(NULLIF(da.display_name,''),NULLIF(da.discovered_hostname,''))) ELSE dt.fqdn END AS destination_name
        FROM topology_diagram_relation r
        JOIN topology_diagram_member sm ON sm.diagram_id=r.diagram_id AND (sm.asset_id=r.source_asset_id OR sm.target_id=r.source_target_id)
        JOIN topology_diagram_member dm ON dm.diagram_id=r.diagram_id AND (dm.asset_id=r.destination_asset_id OR dm.target_id=r.destination_target_id)
        LEFT JOIN asset sa ON sa.id=sm.asset_id LEFT JOIN target st ON st.id=sm.target_id
        LEFT JOIN asset da ON da.id=dm.asset_id LEFT JOIN target dt ON dt.id=dm.target_id
        JOIN site ss ON ss.id=COALESCE(sa.site_id,st.site_id) JOIN site ds ON ds.id=COALESCE(da.site_id,dt.site_id)
        WHERE r.diagram_id=%s ORDER BY ss.name,source_name,ds.name,destination_name,r.id""", (diagram_id,)).fetchall()


@router.get("/options")
def inventory_options():
    with get_connection() as connection:
        return connection.execute("""SELECT 'asset:'||a.id AS id,a.id AS inventory_id,'asset' AS kind,a.site_id,
            concat_ws(' - ',host(a.ip),COALESCE(NULLIF(a.display_name,''),NULLIF(a.discovered_hostname,''))) AS name
            FROM asset a UNION ALL
            SELECT 'target:'||t.id,t.id,'target',t.site_id,t.fqdn FROM target t
            ORDER BY site_id,kind,name""").fetchall()


@router.get("")
def list_diagrams():
    with get_connection() as connection:
        return connection.execute("""SELECT g.id,g.name,g.created_at,
            (SELECT count(*) FROM topology_diagram_member m WHERE m.diagram_id=g.id) AS member_count,
            (SELECT count(*) FROM topology_diagram_relation r WHERE r.diagram_id=g.id) AS relation_count
            FROM topology_diagram g ORDER BY g.name,g.id""").fetchall()


@router.post("", status_code=201)
def create_diagram(value: TypeInput):
    return diagram_write("INSERT INTO topology_diagram(name) VALUES (%s) RETURNING id,name,created_at", (value.name,),
        missing="Diagram not found",duplicate="A diagram with this name already exists")


@router.put("/{diagram_id}")
def update_diagram(diagram_id: int, value: TypeInput):
    return diagram_write("UPDATE topology_diagram SET name=%s WHERE id=%s RETURNING id,name,created_at",(value.name,diagram_id),
        missing="Diagram not found",duplicate="A diagram with this name already exists")


@router.delete("/{diagram_id}")
def delete_diagram(diagram_id: int):
    row = diagram_write("DELETE FROM topology_diagram WHERE id=%s RETURNING id", (diagram_id,),missing="Diagram not found")
    return {"id":row["id"],"deleted":True}


@router.get("/{diagram_id}/members")
def list_members(diagram_id: int):
    with get_connection() as connection:
        diagram_record(connection,diagram_id)
        return members(connection,diagram_id)


@router.post("/{diagram_id}/members", status_code=201)
def create_member(diagram_id: int, value: MemberInput):
    return diagram_write("""INSERT INTO topology_diagram_member(diagram_id,asset_id,target_id)
        SELECT id,%s,%s FROM topology_diagram WHERE id=%s RETURNING id,diagram_id,asset_id,target_id,created_at""",
        (value.asset_id,value.target_id,diagram_id),
        missing="Diagram or resource not found",duplicate="This resource already exists in this diagram")


@router.delete("/{diagram_id}/members/{member_id}")
def delete_member(diagram_id: int, member_id: int):
    row = diagram_write("DELETE FROM topology_diagram_member WHERE id=%s AND diagram_id=%s RETURNING id",(member_id,diagram_id),missing="Diagram or resource not found")
    return {"id":row["id"],"deleted":True}


@router.get("/{diagram_id}/relations")
def list_relations(diagram_id: int):
    with get_connection() as connection:
        diagram_record(connection,diagram_id)
        return relations(connection,diagram_id)


@router.post("/{diagram_id}/relations", status_code=201)
def create_relation(diagram_id: int, value: RelationInput):
    return diagram_write("""INSERT INTO topology_diagram_relation(diagram_id,source_asset_id,source_target_id,destination_asset_id,destination_target_id)
        SELECT id,%s,%s,%s,%s FROM topology_diagram WHERE id=%s RETURNING *""",
        (value.source_asset_id,value.source_target_id,value.destination_asset_id,value.destination_target_id,diagram_id))


@router.put("/{diagram_id}/relations/{relation_id}")
def update_relation(diagram_id: int, relation_id: int, value: RelationInput):
    return diagram_write("""UPDATE topology_diagram_relation SET source_asset_id=%s,source_target_id=%s,destination_asset_id=%s,destination_target_id=%s
        WHERE id=%s AND diagram_id=%s RETURNING *""",
        (value.source_asset_id,value.source_target_id,value.destination_asset_id,value.destination_target_id,relation_id,diagram_id))


@router.delete("/{diagram_id}/relations/{relation_id}")
def delete_relation(diagram_id: int, relation_id: int):
    row = diagram_write("DELETE FROM topology_diagram_relation WHERE id=%s AND diagram_id=%s RETURNING id",(relation_id,diagram_id))
    return {"id":row["id"],"deleted":True}


@router.get("/{diagram_id}/topology")
def diagram_topology(diagram_id: int):
    with get_connection() as connection:
        connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        diagram = diagram_record(connection,diagram_id)
        nodes = members(connection,diagram_id,ports=True)
        links = relations(connection,diagram_id)
    return {"view":diagram,"nodes":nodes,"links":links}
