const API_URL = 'http://localhost:8000/api/v1';

// Tenants / Usuarios
export const fetchTenants = async () => {
    const res = await fetch(`${API_URL}/tenants/?limit=100`);
    if (!res.ok) throw new Error('Error fetching tenants');
    return res.json();
};

export const createTenant = async (tenantData) => {
    const res = await fetch(`${API_URL}/tenants/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(tenantData),
    });
    if (!res.ok) throw new Error('Error creating tenant');
    return res.json();
};

// Comprobantes / Consumos
export const fetchComprobantes = async (skip = 0, limit = 50) => {
    const res = await fetch(`${API_URL}/comprobantes/?skip=${skip}&limit=${limit}`);
    if (!res.ok) throw new Error('Error fetching comprobantes');
    return res.json();
};

// Ejecuciones / Procesos
export const fetchEjecuciones = async (skip = 0, limit = 20) => {
    const res = await fetch(`${API_URL}/ejecuciones/?skip=${skip}&limit=${limit}`);
    if (!res.ok) throw new Error('Error fetching ejecuciones');
    return res.json();
};

export const getHealthCheck = async () => {
    const res = await fetch('http://localhost:8000/health');
    if (!res.ok) throw new Error('Error in healthcheck');
    return res.json();
}
