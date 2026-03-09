from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal


@dataclass
class InfoAutorizacion:
    estado: str = ""
    numero_autorizacion: str = ""
    fecha_autorizacion: datetime | None = None
    ambiente: str = ""


@dataclass
class InfoTributaria:
    ambiente: str = ""
    tipo_emision: str = ""
    razon_social: str = ""
    nombre_comercial: str = ""
    ruc: str = ""
    clave_acceso: str = ""
    cod_doc: str = ""
    estab: str = ""
    pto_emi: str = ""
    secuencial: str = ""
    dir_matriz: str = ""


@dataclass
class DetalleFactura:
    orden: int = 0
    codigo_principal: str = ""
    codigo_auxiliar: str = ""
    descripcion: str = ""
    cantidad: Decimal = Decimal("0")
    precio_unitario: Decimal = Decimal("0")
    descuento: Decimal = Decimal("0")
    precio_total_sin_impuesto: Decimal = Decimal("0")
    iva_codigo: str = ""
    iva_codigo_porcentaje: str = ""
    iva_tarifa: Decimal = Decimal("0")
    iva_base_imponible: Decimal = Decimal("0")
    iva_valor: Decimal = Decimal("0")


@dataclass
class ImpuestoRetencionData:
    codigo: str = ""
    tipo_tributo: str = ""
    codigo_retencion: str = ""
    base_imponible: Decimal = Decimal("0")
    porcentaje_retener: Decimal = Decimal("0")
    valor_retenido: Decimal = Decimal("0")
    cod_doc_sustento: str = ""
    num_doc_sustento: str = ""
    fecha_emision_doc_sustento: str = ""


@dataclass
class PagoData:
    forma_pago: str = ""
    forma_pago_desc: str = ""
    total: Decimal = Decimal("0")
    plazo: str = ""
    unidad_tiempo: str = ""


@dataclass
class ComprobanteParseado:
    autorizacion: InfoAutorizacion = field(default_factory=InfoAutorizacion)
    info_tributaria: InfoTributaria = field(default_factory=InfoTributaria)

    # Tipo de comprobante detectado
    tipo_comprobante: str = ""

    # Cabecera
    fecha_emision: date | None = None
    dir_establecimiento: str = ""
    obligado_contabilidad: str = ""
    contribuyente_especial: str = ""

    # Receptor
    tipo_id_receptor: str = ""
    razon_social_receptor: str = ""
    identificacion_receptor: str = ""

    # Período fiscal (retenciones)
    periodo_fiscal: str = ""

    # Totales
    total_sin_impuestos: Decimal = Decimal("0")
    total_descuento: Decimal = Decimal("0")
    total_iva: Decimal = Decimal("0")
    importe_total: Decimal = Decimal("0")
    moneda: str = "DOLAR"
    propina: Decimal = Decimal("0")

    # Listas
    detalles: list[DetalleFactura] = field(default_factory=list)
    retenciones: list[ImpuestoRetencionData] = field(default_factory=list)
    pagos: list[PagoData] = field(default_factory=list)

    # XML crudo
    xml_raw: str = ""
