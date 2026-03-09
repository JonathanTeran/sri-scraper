import { useState, useEffect } from 'react';
import { Users, Activity, FileText, CheckCircle, XCircle, Clock } from 'lucide-react';
import { fetchTenants, fetchEjecuciones, fetchComprobantes, getHealthCheck } from '../api';

const Overview = () => {
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);

    const [stats, setStats] = useState({
        users: 0,
        executions: 0,
        documents: 0,
        health: null
    });

    const [recentExecutions, setRecentExecutions] = useState([]);

    const loadData = async () => {
        setLoading(true);
        setError(null);
        try {
            // Promise.allSettled avoids failure if one endpoint is down
            const [tenantsRes, execsRes, docsRes, healthRes] = await Promise.allSettled([
                fetchTenants(),
                fetchEjecuciones(0, 5),
                fetchComprobantes(0, 1), // Limiting to 1 just to get the total count if metadata exists, but the API returns items. We might just calculate length.
                getHealthCheck()
            ]);

            const usersCount = tenantsRes.status === 'fulfilled' ? tenantsRes.value.length : 0;
            const execsList = execsRes.status === 'fulfilled' ? execsRes.value : [];
            // If the API returns a list, the total might not be in the response directly without pagination metadata. 
            // For now, let's just use the returned length or a mock if we don't have a count endpoint.
            const docsCount = docsRes.status === 'fulfilled' ? (docsRes.value.total || docsRes.value.length) : 0;
            const dbHealth = healthRes.status === 'fulfilled' ? healthRes.value.status : 'down';

            setStats({
                users: usersCount,
                executions: execsRes.status === 'fulfilled' ? execsRes.value.length : 0, // In a real app, we'd have a count endpoint
                documents: docsCount,
                health: dbHealth
            });
            setRecentExecutions(execsList);
        } catch (err) {
            setError('Error al cargar la información del panel.');
            console.error(err);
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        loadData();
        // Refresh every 30 seconds
        const interval = setInterval(loadData, 30000);
        return () => clearInterval(interval);
    }, []);

    return (
        <div className="page-container animate-fade-in">
            <div className="flex justify-between items-center" style={{ marginBottom: 'var(--space-6)' }}>
                <div>
                    <h1 className="text-gradient">Vista Global</h1>
                    <p style={{ color: 'var(--text-secondary)', marginTop: 'var(--space-1)' }}>
                        Estado del sistema: {stats.health === 'ok' ?
                            <span className="badge badge-success" style={{ marginLeft: 'var(--space-2)' }}>Operativo</span> :
                            <span className="badge badge-error" style={{ marginLeft: 'var(--space-2)' }}>Falla de conexión</span>}
                    </p>
                </div>
                <button className="glass-panel" onClick={loadData} style={{ padding: '0.5rem 1rem', color: 'white', fontWeight: 500 }} disabled={loading}>
                    {loading ? 'Actualizando...' : 'Actualizar Datos'}
                </button>
            </div>

            {error && (
                <div className="glass-panel" style={{ padding: 'var(--space-4)', marginBottom: 'var(--space-6)', border: '1px solid var(--error)' }}>
                    <p style={{ color: 'var(--error)' }}>{error}</p>
                </div>
            )}

            {/* Stats Grid */}
            <div className="dashboard-grid">
                <div className="glass-panel stat-card">
                    <div className="stat-card-header">
                        <span>Usuarios Activos</span>
                        <div className="stat-card-icon stat-primary"><Users size={20} /></div>
                    </div>
                    <div className="stat-card-value">{loading && stats.users === 0 ? '-' : stats.users}</div>
                    <span style={{ fontSize: '0.875rem', color: 'var(--text-tertiary)' }}>Total de RUCs registrados</span>
                </div>

                <div className="glass-panel stat-card">
                    <div className="stat-card-header">
                        <span>Procesos Totales</span>
                        <div className="stat-card-icon stat-warning"><Activity size={20} /></div>
                    </div>
                    <div className="stat-card-value">{loading && stats.executions === 0 ? '-' : stats.executions}</div>
                    <span style={{ fontSize: '0.875rem', color: 'var(--text-tertiary)' }}>Sesiones de scraping ejecutadas</span>
                </div>

                <div className="glass-panel stat-card">
                    <div className="stat-card-header">
                        <span>Comprobantes Descargados</span>
                        <div className="stat-card-icon stat-success"><FileText size={20} /></div>
                    </div>
                    <div className="stat-card-value">{loading && stats.documents === 0 ? '-' : stats.documents}</div>
                    <span style={{ fontSize: '0.875rem', color: 'var(--text-tertiary)' }}>Documentos obtenidos</span>
                </div>
            </div>

            {/* Recent Executions Widget */}
            <div className="glass-panel" style={{ marginTop: 'var(--space-6)', padding: 'var(--space-5)' }}>
                <h2 style={{ fontSize: '1.25rem', marginBottom: 'var(--space-4)', color: 'var(--text-primary)' }}>Ejecuciones Recientes</h2>

                {recentExecutions.length === 0 && !loading ? (
                    <p style={{ color: 'var(--text-secondary)', textAlign: 'center', padding: 'var(--space-4) 0' }}>No hay ejecuciones registradas.</p>
                ) : (
                    <div className="table-container">
                        <table className="data-table">
                            <thead>
                                <tr>
                                    <th>Estado</th>
                                    <th>RUC</th>
                                    <th>Fecha Inicio</th>
                                    <th>Nuevos</th>
                                    <th>Errores</th>
                                </tr>
                            </thead>
                            <tbody>
                                {recentExecutions.map((exec, idx) => (
                                    <tr key={idx}>
                                        <td>
                                            {exec.estado === 'completado' ? (
                                                <span className="badge badge-success"><CheckCircle size={12} style={{ marginRight: '4px' }} /> Éxito</span>
                                            ) : exec.estado === 'error' ? (
                                                <span className="badge badge-error"><XCircle size={12} style={{ marginRight: '4px' }} /> Error</span>
                                            ) : (
                                                <span className="badge badge-warning"><Clock size={12} style={{ marginRight: '4px' }} /> {exec.estado}</span>
                                            )}
                                        </td>
                                        <td style={{ fontWeight: 500 }}>{exec.tenant_ruc}</td>
                                        <td style={{ color: 'var(--text-secondary)' }}>{new Date(exec.fecha_inicio).toLocaleString()}</td>
                                        <td style={{ color: 'var(--success)', fontWeight: 600 }}>+{exec.nuevos}</td>
                                        <td style={{ color: exec.errores > 0 ? 'var(--error)' : 'inherit' }}>{exec.errores}</td>
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

export default Overview;
