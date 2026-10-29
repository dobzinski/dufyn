/* Reuse logical hierarchy rendering and navigation for named multi-view diagrams. */
class SiteLinkMap extends NetworkMap {
    constructor(modal) {
        super(modal, {
            endpoint: id => `/api/links/${encodeURIComponent(id)}/topology`,
            title: "Site Links topology",
            missingMessage: "Site link no longer exists.",
            emptyMessage: "No associations in this site link. Add associations first.",
            subtitle: result => `${result.views.length} views \u00b7 ${result.links.length} associations \u00b7 ${result.nodes.length} assets`,
        });
    }

    orderViews(views, records, links) {
        const viewByNode = new Map(records.map(node => [String(node.id), String(node.topology_view_id)]));
        const byId = new Map(views.map(view => [String(view.id), view]));
        const neighbours = new Map(views.map(view => [String(view.id), new Set()]));
        for (const link of links) {
            const source = viewByNode.get(String(link.source_id)), destination = viewByNode.get(String(link.destination_id));
            if (source === destination || !neighbours.has(source) || !neighbours.has(destination)) continue;
            neighbours.get(source).add(destination);
            neighbours.get(destination).add(source);
        }
        const ordered = [], visited = new Set();
        while (visited.size < byId.size) {
            const remaining = [...byId.keys()].filter(id => !visited.has(id));
            const start = remaining.find(id => neighbours.get(id).size === 1) || remaining[0];
            const stack = [start];
            while (stack.length) {
                const id = stack.pop();
                if (visited.has(id)) continue;
                visited.add(id);
                ordered.push(byId.get(id));
                stack.push(...[...neighbours.get(id)].reverse().filter(next => !visited.has(next)));
            }
        }
        return ordered;
    }

    layout(records) {
        const nodes = new Map();
        this.columns = [];
        const viewIndex = new Map(this.views.map((view,index) => [String(view.id),index]));
        const nodeView = new Map(records.map(node => [String(node.id),viewIndex.get(String(node.topology_view_id))]));
        const longCount = this.links.filter(link => Math.abs(nodeView.get(String(link.source_id)) - nodeView.get(String(link.destination_id))) > 1).length;
        const top = 64 + longCount * 14;
        let left = 0, height = 0;
        for (const view of this.views) {
            super.layout(records.filter(node => String(node.topology_view_id) === String(view.id)));
            const width = this.bounds.width + 48;
            for (const [id,node] of this.nodes) {
                node.x += left + 24;
                node.y += top;
                nodes.set(id,node);
            }
            this.columns.push({view,left,width,height:this.bounds.height + top + 24});
            height = Math.max(height,this.bounds.height + top + 24);
            left += width + 100;
        }
        this.nodes = nodes;
        this.bounds = {width:Math.max(220,left - 100),height:Math.max(94,height)};
    }

    render(records, result) {
        this.links = result.links;
        this.views = this.orderViews(result.views, records, result.links);
        super.render(records);
        const palette = getComputedStyle(document.documentElement);
        const color = (name,fallback) => palette.getPropertyValue(name).trim() || fallback;
        const columns = this.element("g", {"aria-label":"Participating views"});
        for (const column of this.columns) {
            columns.append(this.element("rect", {x:column.left,y:0,width:column.width,height:column.height,rx:10,
                fill:"none",stroke:color("--text-secondary","#566078"),"stroke-width":1.5,"stroke-dasharray":"6 4","vector-effect":"non-scaling-stroke"}));
            columns.append(this.element("text", {x:column.left+16,y:28,fill:color("--text-primary","#172033"),
                style:"font-size:14px;font-weight:600;font-family:inherit"}, `${column.view.site_name} \u00b7 ${column.view.name}`));
        }
        this.scene.prepend(columns);
        const links = this.element("g", {"aria-label":"Site Links"});
        const columnIndex = new Map(this.columns.map((column,index) => [String(column.view.id),index]));
        const edges = result.links.map((link,index) => {
            let left = this.nodes.get(String(link.source_id)), right = this.nodes.get(String(link.destination_id));
            if (!left || !right) return null;
            if (columnIndex.get(String(left.topology_view_id)) > columnIndex.get(String(right.topology_view_id))) [left,right] = [right,left];
            return {link,index,left,right,leftColumn:columnIndex.get(String(left.topology_view_id)),rightColumn:columnIndex.get(String(right.topology_view_id))};
        }).filter(Boolean);
        const ports = new Map(), gutters = new Map();
        const addSlot = (map,key,index) => {
            if (!map.has(key)) map.set(key,[]);
            map.get(key).push(index);
        };
        for (const edge of edges) {
            addSlot(ports,`${edge.left.id}:right`,edge.index);
            addSlot(ports,`${edge.right.id}:left`,edge.index);
            addSlot(gutters,edge.leftColumn,edge.index);
            if (edge.rightColumn - 1 !== edge.leftColumn) addSlot(gutters,edge.rightColumn - 1,edge.index);
        }
        const position = (map,key,index) => {
            const slots = map.get(key);
            return (slots.indexOf(index)+1)/(slots.length+1);
        };
        const rail = (gap,index) => this.columns[gap].left + this.columns[gap].width + 20 + 60*position(gutters,gap,index);
        const obstructed = (node,side,y) => [...this.nodes.values()].some(other => other.id !== node.id
            && String(other.topology_view_id) === String(node.topology_view_id) && y > other.y && y < other.y+94
            && (side === 'right' ? other.x >= node.x+220 : other.x+220 <= node.x));
        let longIndex = 0;
        for (const edge of edges) {
            const {link,index,left,right,leftColumn,rightColumn} = edge;
            const sx=left.x+220, sy=left.y+18+58*position(ports,`${left.id}:right`,index);
            const dx=right.x, dy=right.y+18+58*position(ports,`${right.id}:left`,index);
            const sourceBlocked=obstructed(left,'right',sy), destinationBlocked=obstructed(right,'left',dy);
            const exitY=sourceBlocked ? left.y+114 : sy;
            const entryY=destinationBlocked ? right.y+114 : dy;
            const exitRail=rail(leftColumn,index), entryRail=rail(rightColumn-1,index);
            let path=`M ${sx} ${sy}`;
            if (sourceBlocked) path+=` H ${sx+12} V ${exitY}`;
            path+=` H ${exitRail}`;
            if (rightColumn-leftColumn > 1) {
                // Non-adjacent links travel above the cards, never through another view's assets.
                const lane=48+longIndex++*14;
                path+=` V ${lane} H ${entryRail}`;
            }
            path+=` V ${entryY}`;
            if (destinationBlocked) path+=` H ${dx-12} V ${dy}`;
            path+=` H ${dx}`;
            const stroke=this.edgeGradient(this.scene.querySelector("defs"), `sitelink-edge-${link.id ?? index}`, left, right, sx, sy, dx, dy);
            const item=this.element("path", {class:"network-map-edge",d:path,
                "data-link-id":link.id ?? index,"data-source-id":link.source_id,"data-destination-id":link.destination_id,
                style:`stroke:${stroke};stroke-width:2;opacity:1`,"vector-effect":"non-scaling-stroke"});
            item.append(this.element("title",{},`${left.ip} \u2194 ${right.ip}`));
            links.append(item);
        }
        this.scene.insertBefore(links,this.scene.querySelector(".network-map-node"));
    }
}
