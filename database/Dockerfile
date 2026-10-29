FROM postgres:17-alpine

COPY --chmod=0644 control.sh /opt/dufyn/database/control.sh
COPY --chmod=0644 init/001_schema.sql /opt/dufyn/database/init/001_schema.sql
COPY --chmod=0644 init/001_schema.sql /docker-entrypoint-initdb.d/001_schema.sql
RUN mkdir -p /opt/dufyn/database/restore /opt/dufyn/database/upgrade \
    && chown postgres:postgres /opt/dufyn/database/restore /opt/dufyn/database/upgrade

ENTRYPOINT ["bash", "/opt/dufyn/database/control.sh"]
CMD ["postgres"]
