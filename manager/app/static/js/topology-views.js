/* Logical and physical pages reuse the standard record form, table and Mapping modal. */
class TopologyViewPage extends RecordPage {
    constructor(form, options, map) {
        super(form, options);
        this.map = map;
        this.mapping = createTopologyMapping(this);
        for (const action of ["open-topology", "mapping", "edit-topology"]) {
            this.menu.querySelector(`[data-action="${action}"]`)?.addEventListener("click", () => {
                if (this.deleting) return;
                const record = this.selectedRecord(), opener = this.trigger;
                this.closeActions(true);
                if (!record) return;
                if (action === "mapping") this.mapping.open(record);
                else this.map.open(record.id, opener, action === "edit-topology");
            });
        }
    }
}
