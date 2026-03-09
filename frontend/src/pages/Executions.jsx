import { useEffect, useState } from 'react';
import { fetchEjecuciones } from '../api';
import { Play, CheckCircle, XCircle, Clock } from 'lucide-react';

const Executions = () => {
    const [executions, setExecutions] = useState([]);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        loadExecutions();
    }, []);

    const loadExecutions = async () => {
        setLoading(true);
        try {
            const data = await fetchEjecuciones(0, 50);
            setExecutions(data);
        } catch (error) {
            console.error(error);
        } finally {
            setLoading(false);
        }
    };

    return (
        <div className="page-container animate-fade-in">
            <div className="flex justify-between items-center" style={{ marginBottom: 'var(--space-6)' }}>
                <h1 className="text-gradient">Registros de Procesos</h1>
                <button className="glass-panel" style={{ padding: '0.6rem 1.25rem', color: 'white', fontWeight: 600, display: 'flex', alignItems: 'center', gap: '0.5rem', background: 'var(--accent-primary)' }}>
                    <Play size={18} />
                    Lanzar Scraping Manual
                </button>
            </div>

            <div className="glass-panel">
                {loading ? (
                    <div style={{ padding: 'var(--space-6)', textAlign: 'center', color: 'var(--text-secondary)' }}>Cargando ejecuciones...</div>
                ) : executions.length === 0 ? (
                    <div style={{ padding: 'var(--space-6)', textAlign: 'center', color: 'var(--text-secondary)' }}>No hay ejecuciones registradas.</div>
                ) : (
                    <div className="table-container">
                        <table className="data-table">
                            <thead>
                                <tr>
                                    <th>ID</th>
                                    <th>Tenant RUC</th>
                                    <th>Periodo</th>
                                    <th>Inicio</th>
                                    <th>Fin</th>
                                    <th>Estado</th>
                                    <th>Resumen</th>
                                </tr>
                            </thead>
                            <tbody>
                                {executions.map((e) => (
                                    <tr key={e.id}>
                                        <td style={{ color: 'var(--text-tertiary)', fontSize: '0.875rem' }}>{e.id}</td>
                                        <td style={{ fontWeight: 600 }}>{e.tenant_ruc}</td>
                                        <td>{e.anio} - {e.mes || 'Todo'}</td>
                                        <td style={{ color: 'var(--text-secondary)' }}>{new Date(e.fecha_inicio).toLocaleString()}</td>
                                        <td style={{ color: 'var(--text-secondary)' }}>{e.fecha_fin ? new Date(e.fecha_fin).toLocaleString() : '-'}</td>
                                        <td>
                                            {e.estado === 'completado' ? (
                                                <span className="badge badge-success"><CheckCircle size={12} style={{ marginRight: '4px' }} /> Éxito</span>
                                            ) : e.estado === 'error' ? (
                                                <span className="badge badge-error"><XCircle size={12} style={{ marginRight: '4px' }} /> Error</span>
                                            ) : (
                                                <span className="badge badge-warning"><Clock size={12} style={{ marginRight: '4px' }} /> {e.estado}</span>
                                            )}
                                        </td>
                                        <td>
                                            <div className="flex gap-2">
                                                <span style={{ color: 'var(--success)' }}>+{e.nuevos}</span>
                                                <span style={{ color: e.errores > 0 ? 'var(--error)' : 'var(--text-tertiary)' }}>{e.errores} err</span>
                                            </div>
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

export default Executions;
