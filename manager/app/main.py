from pathlib import Path
from datetime import datetime, timezone

from fastapi import FastAPI, Request, Form
from app.auth import AccessControl, COOKIE
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

import psycopg

from app.texts import router as texts_router
from app.tools import router as tools_router
from app.base64_convert import router as base64_router
from app.backup import router as backup_router
from app.passwords import router as passwords_router
from app.assets import router as assets_router
from app.operating_systems import router as operating_systems_router
from app.dns import router as dns_router
from app.ptr import router as ptr_router
from app.pki import router as pki_router
from app.topology import router as topology_router
from app.diagrams import router as diagrams_router
from app.physical import router as physical_router
from app.ports import router as ports_router
from app.certificates import router as certificates_router
from app.scanners import router as scanners_router
from app.catalog_assignments import router as catalog_assignments_router
from app.services import router as services_router
from app.targets import router as targets_router
from app.config import load_config, load_version
from app.settings_view import settings_sections
from app.controller_status import controller_available
from app.ptr_attention import load_ptr_attention
from app.database import get_connection
from app.newers import router as newers_router
from app.inventory import router as inventory_router
from app.sites import router as sites_router
from app.networks import router as networks_router
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates


APP_DIRECTORY = Path(__file__).resolve().parent
config = load_config()
version = load_version()
app = FastAPI(title="Network Discovery Manager", version=version)
access = AccessControl(lambda: config)
app.middleware("http")(access.middleware)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/api/"):
        response.headers["X-Server-Time"] = datetime.now(timezone.utc).isoformat()
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    if not request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store"
    return response


app.mount("/static", StaticFiles(directory=APP_DIRECTORY / "static"), name="static")
app.include_router(texts_router)
app.include_router(tools_router)
app.include_router(base64_router)
app.include_router(backup_router)
app.include_router(passwords_router)
app.include_router(newers_router)
app.include_router(inventory_router)
app.include_router(assets_router)
app.include_router(operating_systems_router)
app.include_router(dns_router)
app.include_router(ptr_router)
app.include_router(pki_router)
app.include_router(topology_router)
app.include_router(diagrams_router)
app.include_router(physical_router)
app.include_router(ports_router)
app.include_router(certificates_router)
app.include_router(sites_router)
app.include_router(networks_router)
app.include_router(scanners_router)
app.include_router(catalog_assignments_router)
app.include_router(services_router)
app.include_router(targets_router)

templates = Jinja2Templates(directory=APP_DIRECTORY / "templates")


@app.get("/health")
def health():
    return {"status": "ok", "service": "manager"}


@app.get("/api/dns/ptr/attention")
def ptr_attention():
    return load_ptr_attention(config.get("controller", {}).get("url", ""), ca_file=config.get("controller", {}).get("ca_file"))


@app.get("/api/controller/status")
def controller_status():
    available = controller_available(config.get("controller", {}).get("url", ""), ca_file=config.get("controller", {}).get("ca_file"))
    return JSONResponse(content={"available": available}, headers={"Cache-Control": "no-store"})


def render_page(request: Request, page: str, title: str, description: str):
    return templates.TemplateResponse(
        request=request,
        name=f"{page}.html",
        context={
            "newers_days": config.get("newers", {}).get("days", 0),
            "timezones": config["time"]["timezones"],
            "password_manager": config.get("password", {}).get("manager", False),
            "access_enabled": access.enabled,
            "session": access.timeout,
            "page": page,
            "page_title": title,
            "page_description": description,
            "inventory_connected": True,
            "version": version,
            "logo_animate": config.get("logo", {}).get("animate", True),
            "settings_sections": settings_sections(config) if page == "settings" else [],
        },
    )


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    return render_page(request, "dashboard", "Dashboard", "Infrastructure discovery overview")


@app.get("/assets", response_class=HTMLResponse)
def assets(request: Request):
    return render_page(request, "assets", "Assets", "Discovered infrastructure inventory")


@app.get("/ports", response_class=HTMLResponse)
def ports(request: Request):
    return render_page(request, "ports", "Ports", "Discovered TCP port inventory")


@app.get("/certificates", response_class=HTMLResponse)
def certificates(request: Request):
    return render_page(request, "certificates", "Certificates", "TLS certificate inventory")


@app.get("/sites", response_class=HTMLResponse)
def sites(request: Request):
    return render_page(request, "sites", "Sites", "Manage discovery sites")


@app.get("/networks", response_class=HTMLResponse)
def networks(request: Request):
    return render_page(request, "networks", "Networks", "Manage discovery networks")


@app.get("/ports", response_class=HTMLResponse)
def ports(request: Request):
    return render_page(request, "ports", "Ports", "Discovered TCP port inventory")


@app.get("/scanners", response_class=HTMLResponse)
def scanners(request: Request):
    return render_page(request, "scanners", "Scanners", "Manage discovery scanners")


@app.get("/services", response_class=HTMLResponse)
def services(request: Request):
    return render_page(request, "services", "Services", "Manage the port catalog")


@app.get("/targets", response_class=HTMLResponse)
def targets(request: Request):
    return render_page(request, "targets", "Targets", "Manage FQDN monitoring targets")


@app.get("/backup", response_class=HTMLResponse)
def backup_page(request: Request):
    return render_page(request, "backup", "Backup", "Download configuration and database backups")


@app.get("/settings", response_class=HTMLResponse)
def settings(request: Request):
    return render_page(request, "settings", "Settings", "Discovery configuration")


@app.get("/ready")
def ready():
    with get_connection() as connection:
        connection.execute("SELECT 1 FROM asset LIMIT 1")
    return {"status": "ok", "inventory_connected": True}


@app.exception_handler(psycopg.Error)
def database_error(request: Request, error: psycopg.Error):
    return JSONResponse(status_code=503, content={"detail": "Inventory database unavailable"})


@app.get("/web", response_class=HTMLResponse)
def target_results_page(request: Request):
    return render_page(request, "webchecks", "Web Checks", "Check results per target and port")


@app.get("/ptr", response_class=HTMLResponse)
def ptr_page(request: Request):
    return render_page(request, "ptr", "PTR", "Reverse DNS records discovered by scanners")


@app.get("/dns", response_class=HTMLResponse)
def dns_page(request: Request):
    return render_page(request, "dns", "DNS Validation", "Imported DNS zones and host records")


@app.get("/os", response_class=HTMLResponse)
def operating_systems_page(request: Request):
    return render_page(request, "os", "Operating Systems", "Manage operating systems for inventory assets")


@app.get("/types", response_class=HTMLResponse)
def topology_types(request: Request):
    return render_page(request, "types", "Types", "Manage topology asset types")


@app.get("/links", response_class=HTMLResponse)
def topology_links(request: Request):
    return render_page(request, "links", "Site Links", "Connect assets associated with logical views in different Sites")


@app.get("/diagrams", response_class=HTMLResponse)
def diagrams_page(request: Request):
    return render_page(request, "diagrams", "Diagrams", "Manage diagrams across Sites, Assets and Targets")


@app.get("/logical", response_class=HTMLResponse)
def logical_page(request: Request):
    return render_page(request, "logical", "Logical", "Manage logical topology views and associated assets")


@app.get("/physical", response_class=HTMLResponse)
def physical_page(request: Request):
    return render_page(request, "physical", "Physical", "Manage physical topology views, background images and associated assets")


@app.get("/cacerts", response_class=HTMLResponse)
def ca_page(request: Request):
    return RedirectResponse("/selfsigned", status_code=308)


@app.get("/selfsigned", response_class=HTMLResponse)
def issued_certificates_page(request: Request):
    return render_page(request, "selfsignedcerts", "Self-signed", "Manage private certificate authorities and server certificates")


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    if not access.enabled or access.valid(request):
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(request=request, name="login.html", context={
        "next": access.destination(request.query_params.get("next", "/")),
        "error": "", "version": version, "logo_animate": config.get("logo", {}).get("animate", True),
    })


@app.post("/login", response_class=HTMLResponse)
def login_submit(request: Request, username: str = Form(max_length=256), password: str = Form(max_length=1024), next: str = Form("/", max_length=4096)):
    if not access.enabled:
        return RedirectResponse("/", status_code=303)
    token, status = access.authenticate(request, username, password)
    if not token:
        message = "Too many attempts. Please try again in a minute." if status == 429 else "Invalid username or password."
        return templates.TemplateResponse(request=request, name="login.html", context={
            "next": access.destination(next), "error": message, "version": version, "logo_animate": config.get("logo", {}).get("animate", True),
        }, status_code=status)
    response = RedirectResponse(access.destination(next), status_code=303)
    response.set_cookie(COOKIE, token, httponly=True, secure=request.url.scheme == "https", samesite="strict")
    return response


@app.post("/auth/logout")
def logout(request: Request):
    access.logout(request)
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(COOKIE)
    return response


@app.post("/auth/activity")
def session_activity(request: Request):
    if access.enabled and not access.valid(request, touch=True):
        return JSONResponse({"detail": "Authentication required"}, status_code=401)
    return {"session": access.timeout}


@app.get("/auth/status")
def session_status():
    return {"authenticated": True}


@app.get("/passwords", response_class=HTMLResponse)
def passwords_page(request: Request):
    return render_page(request, "passwords", "Passwords", "Generate random passwords")


@app.get("/base64", response_class=HTMLResponse)
def base64_page(request: Request):
    return render_page(request, "base64", "Base64", "Convert between text and Base64")


@app.get("/subnets", response_class=HTMLResponse)
def subnet_page(request: Request):
    return render_page(request, "subnet", "Subnets", "Calculate IPv4 and IPv6 networks")


@app.get("/time", response_class=HTMLResponse)
def time_page(request: Request):
    return render_page(request, "time", "Time", "Convert timestamps, dates and time zones")


@app.get("/inspector", response_class=HTMLResponse)
def certificate_inspector_page(request: Request):
    return render_page(request, "certificate-inspector", "Inspector", "Inspect PEM certificates")


@app.get("/texts", response_class=HTMLResponse)
def texts_page(request: Request):
    return render_page(request, "texts", "Texts", "Validate, format, minify and convert JSON and YAML")
