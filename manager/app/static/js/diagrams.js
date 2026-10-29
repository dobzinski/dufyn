const diagramsPage = new RecordPage(document.getElementById("diagrams-form"), {
    newers: {field: "created_at"},
    emptyMessage: "Create a diagram, then add assets or targets as resources before creating associations.",
    endpoint: "/api/diagrams", table: "#diagrams-table", message: "#diagrams-message", retry: "#diagrams-retry",
    columns: [
        {key:"name",label:"Name"},
        {key:"member_count",label:"Resources",type:"number"},
        {key:"relation_count",label:"Assoc.",type:"number"},
        {key:"created_at",label:"Created",type:"date",render:record=>formatDate(record.created_at)},
    ],
});

class DiagramAssociationPage extends RecordPage {
    async load() {
        if (!this.ownerId) return false;
        this.loading = true;
        try { const loaded = await super.load(); this.syncFields(); return loaded; }
        finally { this.loading = false; }
    }

    async save(event) {
        if (!this.form.reportValidity()) { event.preventDefault(); return; }
        this.saving = true;
        try { await super.save(event); }
        finally { this.saving = false; }
    }
}

class DiagramMembersPage extends DiagramAssociationPage {
    fillSelect(source) {
        let records = source.records || [];
        if (source.name === "member_record") {
            records = records.filter(item => String(item.site_id) === this.choiceValue("member_site") && item.kind === this.choiceValue("member_kind")
                && !(this.records || []).some(member => member.kind === item.kind && String(member[item.kind+"_id"]) === String(item.inventory_id)));
        }
        super.fillSelect({...source,records});
    }

    syncFields() {
        super.syncFields();
        const source = this.options.selects.find(item=>item.name==="member_record");
        const record = source?.records?.find(item=>String(item.id)===this.choiceValue("member_record"));
        for (const kind of ["asset","target"]) {
            const field = this.form.elements.namedItem(kind+"_id");
            field.disabled = !record || record.kind !== kind;
            field.value = field.disabled ? "" : record.inventory_id;
        }
    }
}

class DiagramRelationsPage extends DiagramAssociationPage {
    fillSelect(source) {
        const records = (source.records || []).filter(member => source.name !== "destination_member" || String(member.id) !== this.choiceValue("source_member"));
        super.fillSelect({...source,records:records.map(member=>({...member,name:`${member.kind === "asset" ? "Asset" : "Target"} - ${member.name}`}))});
    }

    syncFields() {
        super.syncFields();
        for (const side of ["source","destination"]) {
            const source = this.options.selects.find(item=>item.name===side+"_member");
            const record = source?.records?.find(item=>String(item.id)===this.choiceValue(side+"_member"));
            for (const kind of ["asset","target"]) {
                const field = this.form.elements.namedItem(side+"_"+kind+"_id");
                field.disabled = !record || record.kind !== kind;
                field.value = field.disabled ? "" : record[kind+"_id"];
            }
        }
        const insufficient = (this.options.selects[0]?.records?.length || 0) < 2;
        this.submit.disabled = !this.optionsReady || insufficient;
        if (insufficient && this.listMessage) this.showMessage(this.listMessage, "Add at least two resources before creating associations.", "error");
    }

    edit(record) {
        this.form.querySelector('[data-choice="source_member"]').value = String(record.source_id);
        this.syncFields();
        super.edit(record);
    }
}

const diagramMembers = new DiagramMembersPage(document.getElementById("member-form"), {
    newers: {field: "created_at"},
    endpoint:"",autoLoad:false,actions:"#member-actions",table:"#member-table",message:"#member-list-message",retry:"#member-retry",
    sortColumn:"site_name",
    selects:[
        {name:"member_site",endpoint:"/api/sites",placeholder:"Select ...",emptyMessage:"Create a site before adding resources."},
        {name:"member_record",endpoint:"/api/diagrams/options",placeholder:"Select ...",emptyMessage:"Create assets or targets before adding resources.",emptyPlaceholder:"No available records in this Site"},
    ],
    columns:[
        {key:"site_name",label:"Site"},
        {key:"kind",label:"Type",value:record=>record.kind==="asset"?"Asset":"Target"},
        {key:"name",label:"Name"},
        {key:"status",label:"Status",render:record=>`<span class="status-badge ${escapeHtml({A:"active",I:"inactive",U:"unknown"}[record.status]||"unknown")}">${escapeHtml({A:"Active",I:"Inactive",U:"Unknown"}[record.status]||"Unknown")}</span>`},
        {key:"created_at",label:"Created",type:"date",render:record=>formatDate(record.created_at)},
    ],
});

const diagramRelations = new DiagramRelationsPage(document.getElementById("relation-form"), {
    newers: {field: "created_at"},
    endpoint:"",autoLoad:false,actions:"#relation-actions",table:"#relation-table",message:"#relation-list-message",retry:"#relation-retry",
    choices:record=>({source_member:String(record.source_id),destination_member:String(record.destination_id)}),
    selects:[
        {name:"source_member",endpoint:"",placeholder:"Select ...",group:"site_name",groupKey:"site_id",required:false},
        {name:"destination_member",endpoint:"",placeholder:"Select ...",group:"site_name",groupKey:"site_id",required:false},
    ],
    columns:[
        {key:"source_site_name",label:"SRC Site"},{key:"source_kind",label:"SRC Type"},{key:"source_name",label:"SRC Name"},
        {key:"destination_site_name",label:"DST Site"},{key:"destination_kind",label:"DST Type"},{key:"destination_name",label:"DST Name"},
        {key:"created_at",label:"Created",type:"date",render:record=>formatDate(record.created_at)},
    ],
});
for (const [kind,page,route] of [["participants",diagramMembers,"members"],["relations",diagramRelations,"relations"]]) {
    const modal=document.getElementById(`diagram-${kind}-modal`);
    const busy=()=>page.loading||page.saving||page.deleting;
    document.getElementById(`diagram-${kind}-close`).addEventListener("click",()=>{
        if(!busy())modal.close();
    });
    modal.addEventListener("cancel",event=>{if(busy())event.preventDefault();});
    modal.addEventListener("close",()=>{
        page.closeActions();page.ownerId=null;
        diagramsPage.deleting=false;
        diagramsPage.load();
    });
    diagramsPage.menu.querySelector(`[data-action="${kind}"]`).addEventListener("click",async()=>{
        if(diagramsPage.deleting)return;
        const record=diagramsPage.selectedRecord();diagramsPage.closeActions(true);
        if(!record)return;
        diagramsPage.deleting=true;
        page.ownerId=record.id;page.options.endpoint=`/api/diagrams/${encodeURIComponent(record.id)}/${route}`;
        if(kind==="relations") {
            // Reload participants on every open, including changes made in the other modal.
            for(const source of page.options.selects)source.endpoint=`/api/diagrams/${encodeURIComponent(record.id)}/members`;
        }
        page.clearMessages();page.reset();page.records=[];page.table?.setData([]);
        document.getElementById(`diagram-${kind}-subtitle`).textContent=record.name;
        modal.showModal();
        await page.load();
    });
}

const diagramsMap = new DiagramMap(document.getElementById("network-map-modal"));
diagramsPage.menu.querySelector('[data-action="open-topology"]').addEventListener("click",()=>{
    if(diagramsPage.deleting)return;
    const record=diagramsPage.selectedRecord(),opener=diagramsPage.trigger;diagramsPage.closeActions(true);
    if(record)diagramsMap.open(record.id,opener);
});
diagramsMap.viewport.addEventListener("click",event=>{if(!event.target.closest(".network-map-node"))diagramsMap.clearSelection();});
