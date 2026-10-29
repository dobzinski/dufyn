/* Diagram graph without directional arrows; shared cards, status gradients, navigation and SVG export. */
class DiagramMap extends NetworkMap {
    constructor(modal) {
        super(modal,{
            endpoint:id=>`/api/diagrams/${encodeURIComponent(id)}/topology`,title:"Diagram",
            missingMessage:"Diagram no longer exists.",emptyMessage:"No resources in this diagram. Add resources first.",
            selectionMessage:"Select an item in the map.",
            subtitle:result=>`${new Set(result.nodes.map(node=>node.site_id)).size} sites \u00b7 ${result.nodes.length} resources \u00b7 ${result.links.length} associations`,
        });
    }

    nodeFooter(node) {return node.site_name || "\u2014";}
    assetName(node) {return node.kind==="target"?node.fqdn:super.assetName(node);}

    layout(records) {
        this.nodes=new Map(records.map(record=>[String(record.id),{...record,parent_id:null,rank:0}]));
        const outgoing=new Map([...this.nodes.keys()].map(id=>[id,[]]));
        const incoming=new Map([...this.nodes.keys()].map(id=>[id,0]));
        for(const link of this.links) {
            const source=String(link.source_id),destination=String(link.destination_id);
            if(!this.nodes.has(source)||!this.nodes.has(destination))continue;
            outgoing.get(source).push(destination);incoming.set(destination,incoming.get(destination)+1);
        }
        const pending=new Set(this.nodes.keys());
        while(pending.size) {
            // Break dependency cycles deterministically while keeping every relation visible.
            const ready=[...pending].filter(id=>incoming.get(id)===0);
            if(!ready.length)ready.push(pending.values().next().value);
            for(const id of ready) {
                pending.delete(id);
                for(const next of outgoing.get(id)) {
                    if(!pending.has(next))continue;
                    this.nodes.get(next).rank=Math.max(this.nodes.get(next).rank,this.nodes.get(id).rank+1);
                    incoming.set(next,incoming.get(next)-1);
                }
            }
        }
        const layers=new Map();
        for(const node of this.nodes.values()) {
            if(!layers.has(node.rank))layers.set(node.rank,[]);
            layers.get(node.rank).push(node);
        }
        const width=Math.max(220,...[...layers.values()].map(layer=>layer.length*260-40));
        for(const [rank,layer] of layers) for(const [index,node] of layer.entries()) {
            node.x=24+(width-(layer.length*260-40))/2+index*260;node.y=24+rank*180;
        }
        this.longLinks=this.links.filter(link=>{
            const source=this.nodes.get(String(link.source_id)),destination=this.nodes.get(String(link.destination_id));
            return source&&destination&&destination.rank!==source.rank+1;
        });
        this.graphWidth=width+48;
        this.bounds={width:this.graphWidth+this.longLinks.length*18,height:(Math.max(0,...layers.keys())+1)*180};
    }

    render(records,result) {
        this.links=result.links;
        super.render(records);
        const defs=this.scene.querySelector("defs"),paths=this.element("g",{"aria-label":"Associations"});
        let lane=0;
        for(const [index,link] of this.links.entries()) {
            const source=this.nodes.get(String(link.source_id)),destination=this.nodes.get(String(link.destination_id));
            if(!source||!destination)continue;
            const sx=source.x+110,sy=source.y+94,dx=destination.x+110,dy=destination.y;
            let path;
            if(destination.rank===source.rank+1) path=`M ${sx} ${sy} V ${(sy+dy)/2} H ${dx} V ${dy}`;
            else {
                const rail=this.graphWidth+lane++*18;
                path=`M ${sx} ${sy} V ${sy+24} H ${rail} V ${dy-16} H ${dx} V ${dy}`;
            }
            const id=`diagram-edge-${link.id??index}`,stroke=this.edgeGradient(defs,id,source,destination,sx,sy,dx,dy);
            const edge=this.element("path",{class:"network-map-edge",d:path,"data-source-id":link.source_id,"data-destination-id":link.destination_id,
                style:`stroke:${stroke};stroke-width:2;opacity:1`,"vector-effect":"non-scaling-stroke"});
            edge.append(this.element("title",{},`${source.ip} \u2194 ${destination.ip}`));paths.append(edge);
        }
        this.scene.insertBefore(paths,this.scene.querySelector(".network-map-node"));
    }

    select(node,item) {
        super.select(node,item);
        for(const label of [...this.details.querySelectorAll('dt')]) {
            if(label.textContent==="IP Address"&&node.kind==="target")label.textContent="FQDN";
            if(label.textContent==="Parent") {label.nextElementSibling.remove();label.remove();}
        }
        const connected = this.links.flatMap(link => String(link.source_id)===String(node.id)
            ? [this.nodes.get(String(link.destination_id))?.ip]
            : String(link.destination_id)===String(node.id) ? [this.nodes.get(String(link.source_id))?.ip] : []);
        const fields={Site:node.site_name,"Record Type":node.kind==="asset"?"Asset":"Target",
            "Connected to":[...new Set(connected.filter(Boolean))].join(", ")};
        for(const [name,value] of Object.entries(fields)) {
            const dt=document.createElement("dt"),dd=document.createElement("dd");dt.textContent=name;dd.textContent=value||"\u2014";this.details.append(dt,dd);
        }
    }
}
