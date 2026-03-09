"""
Extrae comprobantes del SRI: descarga XMLs + extrae datos via modal.

USO:
1. Abre Chrome con: /Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome \
     --user-data-dir=~/.sri-chrome-profile --remote-debugging-port=9222
2. Login SRI → Comprobantes Recibidos → mes/año → captcha → Consultar
3. python scrape_modal.py
"""
import asyncio
import json
import os
import re
import aiohttp
from lxml import etree

XML_DIR = "./xmls"
DATOS_FILE = "./datos_comprobantes.json"
SOAP_URL = "https://cel.sri.gob.ec/comprobantes-electronicos-ws/AutorizacionComprobantesOffline"


async def descargar_xml_soap(clave_acceso: str) -> bytes | None:
    """Descarga XML via SOAP usando la clave de acceso."""
    soap_body = f"""<?xml version="1.0" encoding="UTF-8"?>
<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/"
                  xmlns:ec="http://ec.gob.sri.ws.autorizacion">
  <soapenv:Body>
    <ec:autorizacionComprobante>
      <claveAccesoComprobante>{clave_acceso}</claveAccesoComprobante>
    </ec:autorizacionComprobante>
  </soapenv:Body>
</soapenv:Envelope>"""

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                SOAP_URL,
                data=soap_body.encode("utf-8"),
                headers={"Content-Type": "text/xml; charset=utf-8"},
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status != 200:
                    return None
                xml_resp = await resp.read()

        # Extraer el nodo <autorizacion> completo
        root = etree.fromstring(xml_resp)
        ns = {"ns": "http://ec.gob.sri.ws.autorizacion"}
        aut = root.find(".//ns:autorizacion", ns)
        if aut is None:
            # Try without namespace
            aut = root.find(".//{*}autorizacion")
        if aut is not None:
            return etree.tostring(aut, encoding="unicode").encode("utf-8")
        return None
    except Exception as e:
        print(f"SOAP error: {e}")
        return None


async def extraer_info_tabla(page) -> list[dict]:
    """Extrae info visible de cada fila de la tabla."""
    return await page.evaluate("""
    () => {
        const table = document.getElementById('frmPrincipal:tablaCompRecibidos');
        if (!table) return [];
        const rows = table.querySelectorAll('tbody tr');
        const result = [];
        for (const row of rows) {
            const cells = row.querySelectorAll('td .ui-dt-c');
            if (cells.length < 8) continue;
            const linkEl = row.querySelector('a[id*="j_idt60"]');
            const clave = linkEl ? linkEl.textContent.trim() : '';
            result.push({
                nro: cells[0]?.textContent.trim() || '',
                ruc_razon: cells[1]?.textContent.trim() || '',
                tipo_serie: cells[2]?.textContent.trim() || '',
                clave_acceso: clave,
                fecha_autorizacion: cells[4]?.textContent.trim() || '',
                fecha_emision: cells[5]?.textContent.trim() || '',
                valor_sin_impuestos: cells[6]?.textContent.trim() || '',
                iva: cells[7]?.textContent.trim() || '',
                total: cells[8]?.textContent.trim() || '',
            });
        }
        return result;
    }
    """)


async def extraer_datos_modal(page, fila_idx: int) -> dict | None:
    """Click en fila → abre modal → extrae texto → cierra modal."""
    try:
        link = await page.query_selector(
            f"a[id='frmPrincipal:tablaCompRecibidos:{fila_idx}:j_idt60']"
        )
        if not link:
            return None
        await link.click()

        # Esperar que aparezca el diálogo
        try:
            await page.wait_for_function(
                """() => {
                    const dialogs = document.querySelectorAll('.ui-dialog');
                    for (const d of dialogs) {
                        if (d.offsetParent !== null && d.innerText.length > 50) return true;
                    }
                    return false;
                }""",
                timeout=8000,
            )
        except Exception:
            await asyncio.sleep(2)

        # Extraer datos del modal
        modal_data = await page.evaluate("""
        () => {
            let modalEl = null;
            const dialogs = document.querySelectorAll('.ui-dialog');
            for (const d of dialogs) {
                if (d.offsetParent !== null && d.innerText.length > 50) {
                    if (!modalEl || d.innerText.length > modalEl.innerText.length) {
                        modalEl = d;
                    }
                }
            }
            if (!modalEl) return null;

            const text = modalEl.innerText.trim();
            const data = { _raw_text: text.substring(0, 2000) };

            // Label:value extraction
            for (const line of text.split('\\n')) {
                const m = line.trim().match(/^(.+?):\\s*(.+)$/);
                if (m && m[2].length < 200) {
                    data[m[1].trim().toLowerCase()] = m[2].trim();
                }
            }

            // Specific patterns
            const pats = {
                clave_acceso: /\\b(\\d{49})\\b/,
                ruc_emisor: /(?:RUC|R\\.U\\.C\\.)\\s*:?\\s*(\\d{13})/i,
                numero_completo: /(\\d{3}[-]\\d{3}[-]\\d{9})/,
                fecha_emision: /[Ff]echa\\s*(?:de\\s*)?[Ee]misi[oó]n\\s*:?\\s*(\\d{2}[/\\-]\\d{2}[/\\-]\\d{4})/,
                importe_total: /[Ii]mporte\\s*[Tt]otal\\s*:?\\s*\\$?\\s*([\\d.,]+)/,
                total_sin_impuestos: /[Tt]otal\\s*[Ss]in\\s*[Ii]mpuestos?\\s*:?\\s*\\$?\\s*([\\d.,]+)/,
                valor_iva: /(?:Valor|Total)\\s*IVA\\s*:?\\s*\\$?\\s*([\\d.,]+)/i,
                estado: /(AUTORIZADO|NO AUTORIZADO|ANULADO)/,
            };
            for (const [k, re] of Object.entries(pats)) {
                const m = text.match(re);
                if (m) data[k] = m[1].trim();
            }

            if (/factura/i.test(text)) data.tipo_comprobante = 'FACTURA';
            else if (/nota\\s*de\\s*cr[eé]dito/i.test(text)) data.tipo_comprobante = 'NOTA DE CRÉDITO';
            else if (/retenci[oó]n/i.test(text)) data.tipo_comprobante = 'RETENCIÓN';
            else if (/liquidaci[oó]n/i.test(text)) data.tipo_comprobante = 'LIQUIDACIÓN';

            return data;
        }
        """)

        # Cerrar modal
        await page.evaluate("""
        () => {
            if (typeof dlgPanelDetalleFactura !== 'undefined') {
                dlgPanelDetalleFactura.hide();
                return;
            }
            const btns = document.querySelectorAll('.ui-dialog-titlebar-close');
            for (const b of btns) {
                if (b.offsetParent !== null) { b.click(); return; }
            }
        }
        """)
        await asyncio.sleep(0.8)
        return modal_data

    except Exception as e:
        print(f"error: {e}")
        try:
            await page.keyboard.press("Escape")
            await asyncio.sleep(0.5)
        except Exception:
            pass
        return None


async def procesar_pagina(page, pagina_num: int, resultados: list):
    """Procesa una página: extrae tabla, descarga XMLs, abre modales si falta data."""
    filas = await extraer_info_tabla(page)
    print(f"\n  Página {pagina_num}: {len(filas)} filas")

    for i, fila in enumerate(filas):
        clave = fila.get("clave_acceso", "")
        ruc_razon = fila.get("ruc_razon", "?")
        tipo_serie = fila.get("tipo_serie", "?")
        total = fila.get("total", "?")
        print(f"  [{i+1}/{len(filas)}] {tipo_serie} | {ruc_razon[:35]} | ${total}", end="", flush=True)

        comprobante = {**fila, "pagina": pagina_num}
        xml_guardado = False

        # 1. Intentar descargar XML via SOAP
        if clave and len(clave) >= 49:
            xml_bytes = await descargar_xml_soap(clave)
            if xml_bytes:
                xml_path = os.path.join(XML_DIR, f"{clave}.xml")
                with open(xml_path, "wb") as f:
                    f.write(xml_bytes)
                comprobante["xml_path"] = xml_path
                xml_guardado = True
                print(" [XML]", end="")

        # 2. Abrir modal para datos adicionales
        modal_data = await extraer_datos_modal(page, i)
        if modal_data and not modal_data.get("error"):
            comprobante["modal"] = {k: v for k, v in modal_data.items() if k != "_raw_text"}
            comprobante["modal_raw"] = modal_data.get("_raw_text", "")[:500]
            print(" [MODAL]", end="")

        status = "OK" if (xml_guardado or modal_data) else "SKIP"
        print(f" → {status}")
        resultados.append(comprobante)


async def ir_siguiente_pagina(page) -> bool:
    """Intenta ir a la siguiente página. Retorna True si hay siguiente."""
    return await page.evaluate("""
    () => {
        const next = document.querySelector(
            'a.ui-paginator-next:not(.ui-state-disabled), '
            + 'span.ui-paginator-next:not(.ui-state-disabled)'
        );
        if (next && !next.classList.contains('ui-state-disabled')) {
            next.click();
            return true;
        }
        const scroller = document.querySelector('.rf-ds');
        if (scroller) {
            const current = scroller.querySelector('.rf-ds-act');
            if (current) {
                const num = parseInt(current.textContent);
                for (const a of scroller.querySelectorAll('a')) {
                    if (parseInt(a.textContent) === num + 1) {
                        a.click();
                        return true;
                    }
                }
            }
        }
        return false;
    }
    """)


async def main():
    from playwright.async_api import async_playwright

    print("=" * 60)
    print("EXTRACCIÓN DE COMPROBANTES SRI (XML + MODAL)")
    print("=" * 60)

    print("\n[1] Conectando a Chrome (puerto 9222)...")
    pw = await async_playwright().start()

    try:
        browser = await pw.chromium.connect_over_cdp("http://localhost:9222")
    except Exception as e:
        print(f"\n[!] No se pudo conectar: {e}")
        print("Abre Chrome con: --remote-debugging-port=9222")
        await pw.stop()
        return

    # Buscar tab del SRI
    page = None
    for ctx in browser.contexts:
        for p in ctx.pages:
            if "comprobantes" in p.url.lower() or "sri" in p.url.lower():
                page = p
                break
        if page:
            break

    if not page:
        print("[!] No se encontró tab del SRI.")
        await pw.stop()
        return

    print(f"    Conectado: {page.url[:80]}")

    # Verificar tabla
    num_filas = await page.evaluate("""
    () => {
        const t = document.getElementById('frmPrincipal:tablaCompRecibidos');
        return t ? t.querySelectorAll('tbody tr').length : 0;
    }
    """)

    if num_filas == 0:
        print("[!] No hay filas en la tabla.")
        await pw.stop()
        return

    print(f"\n[2] Tabla encontrada: {num_filas} filas")

    os.makedirs(XML_DIR, exist_ok=True)
    resultados = []
    pagina = 1

    # Procesar primera página
    print(f"\n[3] Procesando comprobantes...")
    print("-" * 60)
    await procesar_pagina(page, pagina, resultados)

    # Siguientes páginas
    while True:
        has_next = await ir_siguiente_pagina(page)
        if not has_next:
            break
        pagina += 1
        await asyncio.sleep(3)
        await procesar_pagina(page, pagina, resultados)

    print("-" * 60)

    # Guardar
    xmls_ok = sum(1 for r in resultados if r.get("xml_path"))
    modals_ok = sum(1 for r in resultados if r.get("modal"))

    output = {
        "total": len(resultados),
        "xmls_descargados": xmls_ok,
        "modals_extraidos": modals_ok,
        "paginas": pagina,
        "comprobantes": resultados,
    }

    with open(DATOS_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n[4] RESUMEN")
    print(f"  Total comprobantes: {len(resultados)}")
    print(f"  XMLs descargados:   {xmls_ok}")
    print(f"  Modales extraidos:  {modals_ok}")
    print(f"  Páginas:            {pagina}")
    print(f"  Guardado en:        {DATOS_FILE}")
    print(f"  XMLs en:            {XML_DIR}/")

    # Listar XMLs
    xmls = [f for f in os.listdir(XML_DIR) if f.endswith(".xml")]
    if xmls:
        print(f"\n  XMLs ({len(xmls)}):")
        for x in xmls[:5]:
            print(f"    {x}")
        if len(xmls) > 5:
            print(f"    ... y {len(xmls)-5} más")

    await pw.stop()
    print("\n[+] Listo.")


if __name__ == "__main__":
    asyncio.run(main())
