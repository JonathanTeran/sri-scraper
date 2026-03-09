"""
Flujo semi-automático:

1. Abre Chrome con remote debugging habilitado
2. Tú haces login, navegas a comprobantes recibidos, seleccionas período,
   y le das click al botón Buscar (pasando el captcha manualmente)
3. Cuando veas los resultados, vuelve aquí y presiona ENTER
4. El script extrae todas las claves de acceso de la tabla
5. Itera todas las páginas automáticamente
6. Descarga los XMLs via SOAP (sin captcha)
7. Parsea los XMLs con el parser del proyecto
8. Inserta los datos en la base de datos PostgreSQL
"""
import asyncio
import json
import os
import re
import subprocess
import sys

import httpx
from lxml import etree


# ── Config ────────────────────────────────────────────────────────────────
XML_DIR = "./xmls"
DATOS_FILE = "./datos_comprobantes.json"

SRI_WS_URL = (
    "https://cel.sri.gob.ec/comprobantes-electronicos-ws/"
    "AutorizacionComprobantesOffline"
)
SOAP_TEMPLATE = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<soapenv:Envelope '
    'xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" '
    'xmlns:ec="http://ec.gob.sri.ws.autorizacion">'
    '<soapenv:Body>'
    '<ec:autorizacionComprobante>'
    '<claveAccesoComprobante>{clave}</claveAccesoComprobante>'
    '</ec:autorizacionComprobante>'
    '</soapenv:Body>'
    '</soapenv:Envelope>'
)


def extraer_xml_de_soap(soap_text: str) -> str | None:
    """Extrae el XML del comprobante de la respuesta SOAP del SRI."""
    try:
        root = etree.fromstring(soap_text.encode("utf-8"))
        for elem in root.iter():
            if elem.tag.endswith("comprobante") or elem.tag == "comprobante":
                text = elem.text
                if text and text.strip():
                    return text.strip()
    except Exception:
        pass
    return None


def extraer_xml_autorizacion_de_soap(soap_text: str) -> str | None:
    """Extrae el XML completo de <autorizacion> de la respuesta SOAP."""
    try:
        root = etree.fromstring(soap_text.encode("utf-8"))
        # Buscar el nodo <autorizacion> (puede tener namespace)
        for elem in root.iter():
            tag = elem.tag.split("}")[-1] if "}" in str(elem.tag) else str(elem.tag)
            if tag == "autorizacion":
                # Serializar el nodo <autorizacion> completo
                return etree.tostring(elem, encoding="unicode")
    except Exception:
        pass
    return None


async def descargar_xmls(claves: list[str]) -> list[dict]:
    """Descarga XMLs por clave de acceso via SOAP.

    Returns list of dicts with keys:
        clave, xml_autorizacion (full XML with wrapper), xml_comprobante, status
    """
    os.makedirs(XML_DIR, exist_ok=True)
    resultados = []

    async with httpx.AsyncClient(timeout=30) as client:
        for idx, clave in enumerate(claves):
            print(f"  [{idx+1}/{len(claves)}] {clave[:20]}...", end=" ")

            # Check cache
            xml_path = os.path.join(XML_DIR, f"{clave}.xml")
            if os.path.exists(xml_path):
                print("(cache)")
                with open(xml_path, "rb") as f:
                    xml_bytes = f.read()
                resultados.append({
                    "clave": clave,
                    "xml_bytes": xml_bytes,
                    "xml_path": xml_path,
                    "status": "cache",
                })
                continue

            try:
                soap_body = SOAP_TEMPLATE.format(clave=clave)
                resp = await client.post(
                    SRI_WS_URL,
                    content=soap_body,
                    headers={
                        "Content-Type": "text/xml; charset=utf-8",
                        "SOAPAction": "",
                    },
                )

                if resp.status_code == 200:
                    # Get full <autorizacion> XML (with wrapper)
                    autorizacion_xml = extraer_xml_autorizacion_de_soap(resp.text)
                    if autorizacion_xml:
                        xml_bytes = autorizacion_xml.encode("utf-8")
                        with open(xml_path, "wb") as f:
                            f.write(xml_bytes)
                        print(f"OK ({len(xml_bytes)} bytes)")
                        resultados.append({
                            "clave": clave,
                            "xml_bytes": xml_bytes,
                            "xml_path": xml_path,
                            "status": "descargado",
                        })
                    else:
                        print("sin XML en SOAP")
                        resultados.append({
                            "clave": clave,
                            "status": "error",
                            "error": "XML no encontrado en SOAP",
                        })
                else:
                    print(f"HTTP {resp.status_code}")
                    resultados.append({
                        "clave": clave,
                        "status": "error",
                        "error": f"HTTP {resp.status_code}",
                    })

            except Exception as e:
                print(f"error: {e}")
                resultados.append({
                    "clave": clave,
                    "status": "error",
                    "error": str(e),
                })

            await asyncio.sleep(0.5)

    return resultados


async def extraer_datos_modal(page, num_filas: int) -> list[dict]:
    """Hace clic en cada fila de la tabla para abrir el modal de detalle,
    extrae el texto del modal y lo parsea a un dict.

    Args:
        page: Playwright page conectada al SRI.
        num_filas: Cantidad de filas en la tabla actual.

    Returns:
        Lista de dicts con datos extraídos del modal de cada comprobante.
    """
    datos_modal = []

    for i in range(num_filas):
        print(f"  [{i+1}/{num_filas}] Abriendo modal...", end=" ")

        try:
            # Clic en el link de clave de acceso de la fila i
            # El patrón JSF es frmPrincipal:tablaCompRecibidos:N:lnkClaveAcceso
            # o puede ser un link en la columna de clave de acceso
            clicked = await page.evaluate(f"""
            () => {{
                // Intentar link específico de clave de acceso
                const lnk = document.getElementById(
                    'frmPrincipal:tablaCompRecibidos:{i}:lnkClaveAcceso'
                );
                if (lnk) {{ lnk.click(); return 'lnkClaveAcceso'; }}

                // Intentar link genérico con outputLink
                const lnk2 = document.getElementById(
                    'frmPrincipal:tablaCompRecibidos:{i}:j_idt60'
                );
                if (lnk2) {{ lnk2.click(); return 'j_idt60'; }}

                // Intentar cualquier link en la fila que abra modal
                const table = document.getElementById('frmPrincipal:tablaCompRecibidos');
                if (table) {{
                    const rows = table.querySelectorAll('tbody tr');
                    if (rows[{i}]) {{
                        // Buscar el primer link que no sea XML ni PDF
                        const links = rows[{i}].querySelectorAll('a');
                        for (const a of links) {{
                            const id = a.id || '';
                            if (!id.includes('lnkXml') && !id.includes('lnkPdf')
                                && !id.includes('lnkRide')) {{
                                a.click();
                                return 'link:' + id;
                            }}
                        }}
                        // Si no hay link, click en la primera celda
                        const firstTd = rows[{i}].querySelector('td');
                        if (firstTd) {{ firstTd.click(); return 'td'; }}
                    }}
                }}
                return null;
            }}
            """)

            if not clicked:
                print("sin link")
                continue

            # Esperar a que aparezca el modal
            await asyncio.sleep(2)

            # Extraer texto del modal
            modal_data = await page.evaluate("""
            () => {
                // Buscar el modal/dialog visible
                const modals = document.querySelectorAll(
                    '.ui-dialog:not([style*="display: none"]):not([style*="display:none"]), '
                    + '.rf-pp-cntr:not([style*="display: none"]), '
                    + '.modal.show, .modal[style*="display: block"], '
                    + '[id*="dlgDetalle"], [id*="dialogDetalle"], '
                    + '[id*="Dialog"][style*="visibility: visible"]'
                );

                let modalEl = null;
                for (const m of modals) {
                    // Pick the one with the most content
                    if (!modalEl || m.innerText.length > modalEl.innerText.length) {
                        modalEl = m;
                    }
                }

                if (!modalEl) {
                    // Try any visible overlay/popup
                    const popups = document.querySelectorAll(
                        '.ui-overlaypanel, .ui-dialog-content, '
                        + '[class*="popup"], [class*="modal"]'
                    );
                    for (const p of popups) {
                        if (p.offsetParent !== null && p.innerText.length > 50) {
                            modalEl = p;
                            break;
                        }
                    }
                }

                if (!modalEl) return null;

                const text = modalEl.innerText.trim();

                // Try to extract structured data from labels
                const data = { _raw: text };
                const fields = [
                    ['clave_acceso', /[Cc]lave.*?[Aa]cceso[:\\s]*(\\d{49})/],
                    ['ruc_emisor', /RUC[:\\s]*(\\d{13})/],
                    ['razon_social', /[Rr]az[oó]n\\s*[Ss]ocial[:\\s]*(.+)/],
                    ['tipo_comprobante', /[Tt]ipo.*?[Cc]omprobante[:\\s]*(.+)/],
                    ['numero', /[Nn][úu]mero[:\\s]*(\\d{3}-\\d{3}-\\d{9})/],
                    ['fecha_emision', /[Ff]echa.*?[Ee]misi[oó]n[:\\s]*(\\d{2}\\/\\d{2}\\/\\d{4})/],
                    ['fecha_autorizacion', /[Ff]echa.*?[Aa]utoriz[:\\s]*(.+)/],
                    ['importe_total', /[Ii]mporte.*?[Tt]otal[:\\s]*\\$?([\\d.,]+)/],
                    ['total_sin_impuestos', /[Tt]otal\\s*[Ss]in.*?[Ii]mpuestos?[:\\s]*\\$?([\\d.,]+)/],
                    ['iva', /IVA[:\\s]*\\$?([\\d.,]+)/],
                    ['estado', /[Ee]stado[:\\s]*(AUTORIZADO|NO AUTORIZADO|ANULADO)/],
                    ['numero_autorizacion', /[Nn][úu]mero.*?[Aa]utoriz[:\\s]*(\\d{10,49})/],
                ];

                for (const [key, regex] of fields) {
                    const match = text.match(regex);
                    if (match) data[key] = match[1].trim();
                }

                // Extract table rows if present (detalles)
                const tables = modalEl.querySelectorAll('table');
                if (tables.length > 0) {
                    const lastTable = tables[tables.length - 1];
                    const rows = lastTable.querySelectorAll('tr');
                    const detalles = [];
                    rows.forEach((row, idx) => {
                        if (idx === 0) return; // skip header
                        const cells = row.querySelectorAll('td');
                        if (cells.length >= 3) {
                            detalles.push({
                                descripcion: cells[0]?.textContent?.trim() || '',
                                cantidad: cells[1]?.textContent?.trim() || '',
                                precio: cells[2]?.textContent?.trim() || '',
                                total: cells[cells.length-1]?.textContent?.trim() || '',
                            });
                        }
                    });
                    if (detalles.length > 0) data._detalles_modal = detalles;
                }

                return data;
            }
            """)

            if modal_data:
                clave = modal_data.get("clave_acceso", "")
                print(f"OK - {modal_data.get('tipo_comprobante', '?')} "
                      f"{modal_data.get('numero', '?')}")
                datos_modal.append(modal_data)
            else:
                print("modal no encontrado")

            # Cerrar el modal
            await page.evaluate("""
            () => {
                // Buscar botón de cerrar en diálogos visibles
                const closeBtns = document.querySelectorAll(
                    '.ui-dialog-titlebar-close, '
                    + '.rf-pp-btn-cntr button, '
                    + 'button.close, [aria-label="Close"], '
                    + 'a[id*="btnCerrar"], button[id*="btnCerrar"], '
                    + '.ui-dialog .ui-icon-closethick'
                );
                for (const btn of closeBtns) {
                    if (btn.offsetParent !== null) {
                        btn.click();
                        return true;
                    }
                }
                // Press Escape as fallback
                document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape'}));
                return false;
            }
            """)
            await asyncio.sleep(1)

        except Exception as e:
            print(f"error: {e}")
            # Try to close any open modal
            try:
                await page.keyboard.press("Escape")
                await asyncio.sleep(0.5)
            except Exception:
                pass

    return datos_modal


async def extraer_info_tabla_completa(page) -> list[dict]:
    """Extrae toda la info visible directamente de las celdas de la tabla,
    sin necesidad de abrir modales. Más rápido que el modal."""
    return await page.evaluate("""
    () => {
        const table = document.getElementById('frmPrincipal:tablaCompRecibidos');
        if (!table) return [];

        const headers = [];
        const headerRow = table.querySelector('thead tr');
        if (headerRow) {
            headerRow.querySelectorAll('th').forEach(th => {
                headers.push(th.textContent.trim().toLowerCase());
            });
        }

        const rows = [];
        table.querySelectorAll('tbody tr').forEach((tr, idx) => {
            const cells = tr.querySelectorAll('td');
            const row = { _fila: idx };

            cells.forEach((td, ci) => {
                const text = td.textContent.trim();
                const header = headers[ci] || 'col_' + ci;

                // Map known header patterns to field names
                if (header.includes('clave') || header.includes('autoriza'))
                    row.clave_acceso = text;
                else if (header.includes('ruc') || header.includes('identificaci'))
                    row.ruc_emisor = text;
                else if (header.includes('raz') || header.includes('social'))
                    row.razon_social = text;
                else if (header.includes('tipo'))
                    row.tipo_comprobante = text;
                else if (header.includes('serie') || header.includes('numero') || header.includes('número'))
                    row.numero = text;
                else if (header.includes('fecha') && header.includes('emis'))
                    row.fecha_emision = text;
                else if (header.includes('fecha') && header.includes('autoriz'))
                    row.fecha_autorizacion = text;
                else if (header.includes('total') || header.includes('importe') || header.includes('valor'))
                    row.importe_total = text;
                else
                    row[header || 'col_' + ci] = text;

                // Also look for 49-digit claves in text
                const claveMatch = text.match(/\\d{49}/);
                if (claveMatch) row.clave_acceso = claveMatch[0];
            });

            // Check for XML download link
            const xmlLink = tr.querySelector('a[id*="lnkXml"]');
            row.has_xml_link = !!xmlLink;

            if (row.clave_acceso || row.ruc_emisor) rows.push(row);
        });

        return rows;
    }
    """)


async def parsear_e_insertar_db(resultados: list[dict], tenant_id: str | None = None):
    """Parsea XMLs descargados e inserta en la BD usando el parser del proyecto."""
    # Import lazily to avoid issues if DB is not configured
    try:
        from parsers.xml_parser import parse_comprobante_sri, comprobante_to_dict
    except ImportError:
        print("[!] No se pudo importar el parser. Guardando solo datos básicos.")
        return None

    parsed_data = []
    errores_parse = []

    for r in resultados:
        if r.get("status") == "error":
            continue

        xml_bytes = r.get("xml_bytes")
        if not xml_bytes:
            continue

        clave = r["clave"]
        try:
            comp = parse_comprobante_sri(xml_bytes)
            data = comprobante_to_dict(comp)
            data["_detalles"] = [
                {
                    "orden": d.orden,
                    "codigo_principal": d.codigo_principal,
                    "descripcion": d.descripcion,
                    "cantidad": str(d.cantidad),
                    "precio_unitario": str(d.precio_unitario),
                    "descuento": str(d.descuento),
                    "precio_total_sin_impuesto": str(d.precio_total_sin_impuesto),
                    "iva_codigo": d.iva_codigo,
                    "iva_codigo_porcentaje": d.iva_codigo_porcentaje,
                    "iva_tarifa": str(d.iva_tarifa),
                    "iva_base_imponible": str(d.iva_base_imponible),
                    "iva_valor": str(d.iva_valor),
                }
                for d in comp.detalles
            ]
            data["_retenciones"] = [
                {
                    "codigo": ret.codigo,
                    "tipo_tributo": ret.tipo_tributo,
                    "codigo_retencion": ret.codigo_retencion,
                    "base_imponible": str(ret.base_imponible),
                    "porcentaje_retener": str(ret.porcentaje_retener),
                    "valor_retenido": str(ret.valor_retenido),
                    "cod_doc_sustento": ret.cod_doc_sustento,
                    "num_doc_sustento": ret.num_doc_sustento,
                }
                for ret in comp.retenciones
            ]
            data["_pagos"] = [
                {
                    "forma_pago": p.forma_pago,
                    "forma_pago_desc": p.forma_pago_desc,
                    "total": str(p.total),
                    "plazo": p.plazo,
                    "unidad_tiempo": p.unidad_tiempo,
                }
                for p in comp.pagos
            ]
            # Convert Decimal to str for JSON serialization
            for k, v in data.items():
                if hasattr(v, "as_tuple"):  # Decimal
                    data[k] = str(v)
                elif isinstance(v, (type(None), str, int, float, bool, list, dict)):
                    pass
                else:
                    data[k] = str(v)

            parsed_data.append(data)
            print(f"  Parseado: {data.get('tipo_comprobante', '?')} "
                  f"{data.get('numero_completo', '?')} "
                  f"- {data.get('razon_social_emisor', '?')[:40]}")

        except Exception as e:
            errores_parse.append({"clave": clave, "error": str(e)})
            print(f"  Error parseando {clave[:20]}: {e}")

    print(f"\n  Parseados: {len(parsed_data)}, Errores: {len(errores_parse)}")

    # Try to insert into DB
    if tenant_id and parsed_data:
        inserted = await _insertar_en_db(parsed_data, tenant_id)
        print(f"  Insertados en BD: {inserted}")

    return parsed_data


async def _insertar_en_db(parsed_data: list[dict], tenant_id: str) -> int:
    """Inserta comprobantes parseados en la base de datos."""
    try:
        from db.session import get_db
        from db.models.comprobante import Comprobante
        from db.models.detalle import DetalleComprobante
        from db.models.retencion import ImpuestoRetencion
        from db.models.pago import Pago
        from sqlalchemy import select
        from decimal import Decimal
    except ImportError as e:
        print(f"  [!] No se pudo importar DB: {e}")
        return 0

    inserted = 0
    async for session in get_db():
        for data in parsed_data:
            clave = data.get("clave_acceso", "")
            if not clave:
                continue

            # Check duplicate
            existing = await session.execute(
                select(Comprobante.id).where(
                    Comprobante.tenant_id == tenant_id,
                    Comprobante.clave_acceso == clave,
                )
            )
            if existing.scalar_one_or_none():
                continue

            # Extract nested data before creating Comprobante
            detalles_data = data.pop("_detalles", [])
            retenciones_data = data.pop("_retenciones", [])
            pagos_data = data.pop("_pagos", [])

            # Remove non-model fields
            data.pop("xml_raw", None)
            clean = {k: v for k, v in data.items() if not k.startswith("_")}

            # Convert str back to Decimal for numeric fields
            decimal_fields = [
                "total_sin_impuestos", "total_descuento", "total_iva",
                "importe_total", "propina",
            ]
            for f in decimal_fields:
                if f in clean and isinstance(clean[f], str):
                    clean[f] = Decimal(clean[f])

            clean["tenant_id"] = tenant_id
            comp_obj = Comprobante(**clean)
            session.add(comp_obj)
            await session.flush()

            # Detalles
            for det in detalles_data:
                for f in ["cantidad", "precio_unitario", "descuento",
                           "precio_total_sin_impuesto", "iva_tarifa",
                           "iva_base_imponible", "iva_valor"]:
                    if f in det and isinstance(det[f], str):
                        det[f] = Decimal(det[f])
                det["comprobante_id"] = comp_obj.id
                det["tenant_id"] = tenant_id
                session.add(DetalleComprobante(**det))

            # Retenciones
            for ret in retenciones_data:
                for f in ["base_imponible", "porcentaje_retener", "valor_retenido"]:
                    if f in ret and isinstance(ret[f], str):
                        ret[f] = Decimal(ret[f])
                ret["comprobante_id"] = comp_obj.id
                ret["tenant_id"] = tenant_id
                session.add(ImpuestoRetencion(**ret))

            # Pagos
            for pago in pagos_data:
                if "total" in pago and isinstance(pago["total"], str):
                    pago["total"] = Decimal(pago["total"])
                pago["comprobante_id"] = comp_obj.id
                pago["tenant_id"] = tenant_id
                session.add(Pago(**pago))

            inserted += 1

        await session.commit()

    return inserted


async def main():
    print("=" * 60)
    print("SCRAPE SEMI-AUTOMÁTICO DE COMPROBANTES SRI")
    print("=" * 60)

    # Paso 1: Abrir Chrome con debugging
    print("\n[1] Abriendo Chrome con remote debugging...")
    chrome_path = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    user_data = os.path.expanduser("~/.sri-chrome-profile")

    chrome_proc = subprocess.Popen([
        chrome_path,
        f"--user-data-dir={user_data}",
        "--remote-debugging-port=9222",
        "--no-first-run",
        "--no-default-browser-check",
        "https://srienlinea.sri.gob.ec/comprobantes-electronicos-internet/"
        "pages/consultas/recibidos/comprobantesRecibidos.jsf",
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    print("\n[2] Chrome abierto. Haz lo siguiente:")
    print("    a) Login en el SRI")
    print("    b) Navega a Comprobantes Electrónicos > Recibidos")
    print("    c) Selecciona año, mes y tipo")
    print("    d) Click en CONSULTAR (pasa el captcha)")
    print("    e) Cuando veas la tabla de comprobantes,")
    print("       vuelve aquí y presiona ENTER")
    print()

    input(">>> Presiona ENTER cuando veas la tabla de resultados...")

    # Paso 2: Conectar via CDP
    print("\n[3] Conectando a Chrome...")
    from playwright.async_api import async_playwright

    pw = await async_playwright().start()
    browser = await pw.chromium.connect_over_cdp("http://localhost:9222")

    # Buscar la tab del SRI
    contexts = browser.contexts
    page = None
    for ctx in contexts:
        for p in ctx.pages:
            if "comprobantes" in p.url.lower() or "sri" in p.url.lower():
                page = p
                break
        if page:
            break

    if not page:
        print("[!] No se encontró la tab del SRI. Tabs disponibles:")
        for ctx in contexts:
            for p in ctx.pages:
                print(f"    {p.url[:80]}")
        await pw.stop()
        chrome_proc.terminate()
        return

    print(f"    Conectado a: {page.url[:80]}")

    # Paso 3: Extraer info de la tabla + claves de acceso de TODAS las páginas
    print("\n[4] Extrayendo datos de la tabla...")
    todas_claves = []
    todos_datos_tabla = []
    pagina = 1

    while True:
        print(f"\n  Página {pagina}...")

        # Extraer claves de la tabla de comprobantes
        claves_pagina = await page.evaluate("""
        () => {
            const claves = [];
            // Buscar en la tabla de comprobantes recibidos
            const table = document.getElementById('frmPrincipal:tablaCompRecibidos');
            if (table) {
                // Buscar links de XML que contienen la clave de acceso
                const links = table.querySelectorAll('a[id*="lnkXml"]');
                links.forEach(link => {
                    const onclick = link.getAttribute('onclick') || '';
                    // Extraer clave del onclick (49 dígitos)
                    const match = onclick.match(/(\\d{49})/);
                    if (match) claves.push(match[1]);
                });

                // También buscar en celdas de la tabla
                if (claves.length === 0) {
                    const cells = table.querySelectorAll('td');
                    cells.forEach(td => {
                        const text = td.textContent.trim();
                        const match = text.match(/^(\\d{49})$/);
                        if (match) claves.push(match[1]);
                    });
                }
            }

            // Fallback: buscar en todo el panel
            if (claves.length === 0) {
                const panel = document.getElementById('frmPrincipal:panelListaComprobantes');
                const html = panel ? panel.innerHTML : document.body.innerHTML;
                const regex = /\\b(\\d{49})\\b/g;
                let m;
                while ((m = regex.exec(html)) !== null) {
                    claves.push(m[1]);
                }
            }

            return [...new Set(claves)];
        }
        """)

        # También extraer info visible de la tabla (sin modal)
        info_tabla = await extraer_info_tabla_completa(page)
        if info_tabla:
            todos_datos_tabla.extend(info_tabla)
            print(f"    Info tabla: {len(info_tabla)} filas")

        print(f"    Claves encontradas: {len(claves_pagina)}")

        if not claves_pagina:
            if pagina == 1:
                print("    [!] No se encontraron claves en la tabla.")
                # Debug: show table info
                debug = await page.evaluate("""
                () => {
                    const table = document.getElementById('frmPrincipal:tablaCompRecibidos');
                    const panel = document.getElementById('frmPrincipal:panelListaComprobantes');
                    return {
                        hasTable: !!table,
                        hasPanel: !!panel,
                        tableRows: table ? table.querySelectorAll('tr').length : 0,
                        xmlLinks: table ? table.querySelectorAll('a[id*="lnkXml"]').length : 0,
                        bodyLen: document.body.innerHTML.length,
                        longNums: (document.body.innerHTML.match(/\\d{40,50}/g) || []).length,
                    };
                }
                """)
                print(f"    Debug: {json.dumps(debug)}")
            break

        todas_claves.extend(claves_pagina)

        # Intentar ir a la siguiente página
        has_next = await page.evaluate("""
        () => {
            // PrimeFaces paginator
            const next = document.querySelector(
                'a.ui-paginator-next:not(.ui-state-disabled), '
                + 'span.ui-paginator-next:not(.ui-state-disabled), '
                + 'a[class*="rf-ds"][class*="next"]:not([class*="dis"])'
            );
            if (next && !next.classList.contains('ui-state-disabled')) {
                next.click();
                return true;
            }

            // RichFaces DataScroller - buscar link de página siguiente
            const scroller = document.querySelector('.rf-ds');
            if (scroller) {
                const links = scroller.querySelectorAll('a');
                const current = scroller.querySelector('.rf-ds-act');
                if (current) {
                    const currentNum = parseInt(current.textContent);
                    for (const link of links) {
                        if (parseInt(link.textContent) === currentNum + 1) {
                            link.click();
                            return true;
                        }
                    }
                }
            }
            return false;
        }
        """)

        if not has_next:
            print("    Última página alcanzada.")
            break

        pagina += 1
        await asyncio.sleep(3)  # Esperar carga AJAX

    # Deduplicar preservando orden
    todas_claves = list(dict.fromkeys(todas_claves))
    print(f"\n  Total claves únicas: {len(todas_claves)}")

    if not todas_claves:
        print("\n[!] No se encontraron claves. Abortando.")
        await pw.stop()
        return

    # Paso 4: Descargar XMLs via SOAP
    print(f"\n[5] Descargando {len(todas_claves)} XMLs via SOAP...")
    resultados = await descargar_xmls(todas_claves)

    exitosos = [r for r in resultados if r["status"] != "error"]
    errores = [r for r in resultados if r["status"] == "error"]
    print(f"\n    Descargados: {len(exitosos)}, Errores: {len(errores)}")

    # Paso 5a: Si hay errores SOAP, intentar extraer datos via modal
    datos_modal = []
    if errores:
        print(f"\n[5b] {len(errores)} claves fallaron SOAP. ¿Extraer via modal? (s/N)")
        resp = input(">>> ").strip().lower()
        if resp == "s":
            # Necesitamos saber qué filas corresponden a las claves fallidas
            # Por ahora extraer modal de todas las filas visibles
            num_filas = await page.evaluate("""
            () => {
                const table = document.getElementById('frmPrincipal:tablaCompRecibidos');
                return table ? table.querySelectorAll('tbody tr').length : 0;
            }
            """)
            if num_filas > 0:
                print(f"    Extrayendo datos de {num_filas} filas via modal...")
                datos_modal = await extraer_datos_modal(page, num_filas)
                print(f"    Extraídos: {len(datos_modal)} modales")

    # Paso 6: Parsear XMLs con el parser del proyecto
    parsed_data = []
    if exitosos:
        print(f"\n[6] Parseando {len(exitosos)} XMLs...")
        parsed_data = await parsear_e_insertar_db(resultados)

    # Paso 7: Guardar JSON con todos los datos recopilados
    all_data = {
        "parsed_xml": [],
        "tabla_info": todos_datos_tabla,
        "modal_info": datos_modal,
    }

    if parsed_data:
        for d in parsed_data:
            clean = {k: v for k, v in d.items()
                     if not k.startswith("_") and k != "xml_raw"}
            all_data["parsed_xml"].append(clean)

    with open(DATOS_FILE, "w", encoding="utf-8") as f:
        json.dump(all_data, f, ensure_ascii=False, indent=2, default=str)
    print(f"    Datos guardados en: {DATOS_FILE}")

    # Resumen
    print(f"\n{'='*60}")
    print("RESUMEN")
    print(f"{'='*60}")
    print(f"  Claves encontradas: {len(todas_claves)}")
    print(f"  Info tabla:         {len(todos_datos_tabla)} filas")
    print(f"  XMLs descargados:   {len(exitosos)}")
    print(f"  XMLs parseados:     {len(parsed_data) if parsed_data else 0}")
    print(f"  Datos modal:        {len(datos_modal)}")
    print(f"  Errores descarga:   {len(errores)}")
    print(f"  XMLs en {XML_DIR}/")
    print(f"  Datos en {DATOS_FILE}")

    if parsed_data:
        d = parsed_data[0]
        print(f"\n  Muestra del primer comprobante:")
        for k in ["tipo_comprobante", "numero_completo", "ruc_emisor",
                   "razon_social_emisor", "fecha_emision", "importe_total"]:
            if k in d:
                print(f"    {k}: {d[k]}")

    await pw.stop()
    print("\n[+] Listo. Chrome sigue abierto.")


if __name__ == "__main__":
    asyncio.run(main())
