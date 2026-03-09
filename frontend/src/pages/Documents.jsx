import { useEffect, useState } from 'react';
import { fetchComprobantes } from '../api';
import { Download, FileText, Search } from 'lucide-react';

const Documents = () => {
    const [documents, setDocuments] = useState([]);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        loadDocuments();
    }, []);

    const loadDocuments = async () => {
        setLoading(true);
        try {
            const resp = await fetchComprobantes(0, 50);
            // Depending on backend pagination format, it could be an array or an object
            const dataList = resp.items || resp;
            setDocuments(Array.isArray(dataList) ? dataList : []);
        } catch (error) {
            console.error(error);
        } finally {
            setLoading(false);
        }
    };

    return (
        <div className="page-container animate-fade-in">
            <div className="flex justify-between items-center" style={{ marginBottom: 'var(--space-6)' }}>
                <h1 className="text-gradient">Consumos (Comprobantes)</h1>
                <div className="flex gap-3">
                    <div className="glass-panel flex items-center" style={{ padding: '0.4rem 1rem', background: 'rgba(255,255,255,0.02)' }}>
                        <Search size={16} style={{ color: 'var(--text-tertiary)', marginRight: '0.5rem' }} />
                        <input
                            type="text"
                            placeholder="Buscar RUC, Clave..."
                            style={{ background: 'transparent', border: 'none', color: 'var(--text-primary)', outline: 'none', width: '200px' }}
                        />
                    </div>
                    <button className="glass-panel" style={{ padding: '0.6rem 1.25rem', color: 'white', fontWeight: 600, display: 'flex', alignItems: 'center', gap: '0.5rem', background: 'var(--accent-primary)' }}>
                        <Download size={18} />
                        Exportar Excel
                    </button>
                </div>
            </div>

            <div className="glass-panel">
                {loading ? (
                    <div style={{ padding: 'var(--space-6)', textAlign: 'center', color: 'var(--text-secondary)' }}>Cargando documentos...</div>
                ) : documents.length === 0 ? (
                    <div style={{ padding: 'var(--space-6)', textAlign: 'center', color: 'var(--text-secondary)' }}>No hay documentos descargados.</div>
                ) : (
                    <div className="table-container">
                        <table className="data-table">
                            <thead>
                                <tr>
                                    <th>Tipo</th>
                                    <th>Emisor</th>
                                    <th>Receptor (Tenant)</th>
                                    <th>Fecha Emisión</th>
                                    <th>Clave Acceso</th>
                                    <th>Total</th>
                                    <th>XML</th>
                                </tr>
                            </thead>
                            <tbody>
                                {documents.map((d) => (
                                    <tr key={d.id || d.clave_acceso}>
                                        <td>
                                            <span className="badge badge-neutral">{d.tipo_comprobante}</span>
                                        </td>
                                        <td>
                                            <div className="flex flex-col">
                                                <span style={{ fontWeight: 500 }}>{d.razon_social_emisor}</span>
                                                <span style={{ fontSize: '0.75rem', color: 'var(--text-secondary)' }}>{d.ruc_emisor}</span>
                                            </div>
                                        </td>
                                        <td>{d.tenant_ruc}</td>
                                        <td style={{ color: 'var(--text-secondary)' }}>{new Date(d.fecha_emision).toLocaleDateString()}</td>
                                        <td>
                                            <span style={{
                                                fontFamily: 'monospace',
                                                fontSize: '0.75rem',
                                                background: 'rgba(255,255,255,0.05)',
                                                padding: '0.2rem 0.4rem',
                                                borderRadius: '4px',
                                                display: 'inline-block',
                                                maxWidth: '200px',
                                                overflow: 'hidden',
                                                textOverflow: 'ellipsis'
                                            }}>
                                                {d.clave_acceso}
                                            </span>
                                        </td>
                                        <td style={{ fontWeight: 600, color: 'var(--success)' }}>${Number(d.importe_total || 0).toFixed(2)}</td>
                                        <td>
                                            <button style={{ color: 'var(--accent-primary)', display: 'flex', alignItems: 'center', gap: '4px' }}>
                                                <FileText size={16} /> XML
                                            </button>
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                )}
            </div>
        </div>
    );
};

export default Documents;
