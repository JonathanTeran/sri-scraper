import { useEffect, useState } from 'react';
import { fetchTenants } from '../api';
import { Plus, User, FileKey, CheckCircle, XCircle } from 'lucide-react';

const Tenants = () => {
    const [tenants, setTenants] = useState([]);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        loadTenants();
    }, []);

    const loadTenants = async () => {
        setLoading(true);
        try {
            const data = await fetchTenants();
            setTenants(data);
        } catch (error) {
            console.error(error);
        } finally {
            setLoading(false);
        }
    };

    return (
        <div className="page-container animate-fade-in">
            <div className="flex justify-between items-center" style={{ marginBottom: 'var(--space-6)' }}>
                <h1 className="text-gradient">Usuarios Registrados</h1>
                <button className="glass-panel" style={{ padding: '0.6rem 1.25rem', color: 'white', fontWeight: 600, display: 'flex', alignItems: 'center', gap: '0.5rem', background: 'var(--accent-primary)' }}>
                    <Plus size={18} />
                    Nuevo Usuario
                </button>
            </div>

            <div className="glass-panel">
                {loading ? (
                    <div style={{ padding: 'var(--space-6)', textAlign: 'center', color: 'var(--text-secondary)' }}>Cargando usuarios...</div>
                ) : tenants.length === 0 ? (
                    <div style={{ padding: 'var(--space-6)', textAlign: 'center', color: 'var(--text-secondary)' }}>No hay usuarios (tenants) registrados.</div>
                ) : (
                    <div className="table-container">
                        <table className="data-table">
                            <thead>
                                <tr>
                                    <th>RUC</th>
                                    <th>Razón Social</th>
                                    <th>Credenciales SRI</th>
                                    <th>Estado Script</th>
                                    <th>Creado</th>
                                    <th>Acciones</th>
                                </tr>
                            </thead>
                            <tbody>
                                {tenants.map((t) => (
                                    <tr key={t.ruc}>
                                        <td style={{ fontWeight: 600 }}>{t.ruc}</td>
                                        <td>{t.razon_social || 'No especificada'}</td>
                                        <td>
                                            {t.sri_password_encrypted ? (
                                                <span className="badge badge-success"><FileKey size={12} style={{ marginRight: '4px' }} /> Guardadas</span>
                                            ) : (
                                                <span className="badge badge-warning">Sin contraseña</span>
                                            )}
                                        </td>
                                        <td>
                                            {t.is_active ?
                                                <span className="badge badge-success">Activo</span> :
                                                <span className="badge badge-neutral">Inactivo</span>}
                                        </td>
                                        <td style={{ color: 'var(--text-secondary)' }}>{new Date(t.created_at).toLocaleDateString()}</td>
                                        <td>
                                            <button style={{ color: 'var(--accent-primary)', fontWeight: 500, fontSize: '0.875rem' }}>Editar</button>
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

export default Tenants;
