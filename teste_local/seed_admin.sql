-- Dados de teste: os quatro papéis e um administrador no tenant fixo.
-- As tabelas são as criadas pelo Hibernate (ddl-auto: update), que têm
-- version e deleted_at em todas (ver ALTERACOES.md, relato 8).
-- Login: admin@teste.local / Admin@123, tenant 00000000-0000-0000-0000-000000000001

CREATE EXTENSION IF NOT EXISTS pgcrypto;

INSERT INTO roles (id, tenant_id, name, description, created_at, updated_at, version)
SELECT r.id::uuid, '00000000-0000-0000-0000-000000000001', r.name, r.descr, now(), now(), 0
FROM (VALUES
    ('00000000-0000-0000-0000-0000000000a1', 'ADMIN',    'Administrador'),
    ('00000000-0000-0000-0000-0000000000a2', 'MANAGER',  'Gerente'),
    ('00000000-0000-0000-0000-0000000000a3', 'OPERATOR', 'Operador'),
    ('00000000-0000-0000-0000-0000000000a4', 'VIEWER',   'Leitura')
) AS r(id, name, descr)
WHERE NOT EXISTS (SELECT 1 FROM roles x WHERE x.id = r.id::uuid);

INSERT INTO users (id, tenant_id, email, password_hash, full_name, status,
                   token_version, failed_login_attempts, created_at, updated_at, version)
SELECT '00000000-0000-0000-0000-0000000000b1', '00000000-0000-0000-0000-000000000001',
       'admin@teste.local', crypt('Admin@123', gen_salt('bf', 10)), 'Administrador de Teste',
       'ACTIVE', 1, 0, now(), now(), 0
WHERE NOT EXISTS (SELECT 1 FROM users WHERE id = '00000000-0000-0000-0000-0000000000b1');

INSERT INTO user_roles (id, tenant_id, user_id, role_id, created_at, updated_at, version)
SELECT '00000000-0000-0000-0000-0000000000c1', '00000000-0000-0000-0000-000000000001',
       '00000000-0000-0000-0000-0000000000b1', '00000000-0000-0000-0000-0000000000a1',
       now(), now(), 0
WHERE NOT EXISTS (SELECT 1 FROM user_roles WHERE id = '00000000-0000-0000-0000-0000000000c1');
