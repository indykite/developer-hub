-- Copyright (c) 2026 IndyKite
-- ERP invoices seed: the account fee / statement invoices of the ibac
-- dataset. The rows mirror the Invoice nodes of the knowledge graph
-- (instant-stack data/ibac): same external_ids, customer_id = the Customer
-- record a Person IS_CUSTOMER of, amounts and statuses as captured. The
-- database knows nothing about who may see what - AuthZEN search/resource
-- over the graph selects the rows per caller before any SQL runs.

CREATE TABLE IF NOT EXISTS invoices (
    external_id   TEXT PRIMARY KEY,
    customer_id   TEXT NOT NULL,
    customer_name TEXT NOT NULL,
    policy_number TEXT,
    amount        NUMERIC(10, 2) NOT NULL,
    currency      TEXT NOT NULL DEFAULT 'USD',
    due_date      DATE NOT NULL,
    status        TEXT NOT NULL,
    description   TEXT
);

INSERT INTO invoices VALUES
  ('inv-cb-001', 'cust-bambi',     'Bambi Stardust',         NULL,  14.50, 'CAD', '2025-01-31', 'open',    'Chequing account monthly fee - acc_bambi_chequing'),
  ('inv-cb-002', 'cust-bambi',     'Bambi Stardust',         NULL, 923.10, 'CAD', '2025-02-01', 'open',    'Mortgage interest installment - acc_bambi_mortgage'),
  ('inv-cb-003', 'cust-cornelius', 'Cornelius Pickleworth',  NULL,  85.00, 'CAD', '2025-01-15', 'paid',    'Youth savings card replacement - acc_cornelius_youth'),
  ('inv-cb-004', 'cust-eustorgio', 'Eustorgio Rodriguez',    NULL,  85.00, 'CAD', '2025-04-15', 'open',    'Youth savings card replacement - acc_eustorgio_youth'),
  ('inv-cb-005', 'cust-marmaduke', 'Marmaduke Doomington',   NULL, 312.40, 'CAD', '2025-02-10', 'overdue', 'Personal loan installment - acc_marmaduke_loan'),
  ('inv-cb-006', 'cust-berengar',  'Berengar Doomington',    NULL, 312.40, 'CAD', '2025-03-10', 'open',    'Youth savings overdraft fee - acc_berengar_youth'),
  ('inv-cb-007', 'cust-cookie',    'Cookie Malhotra',        NULL, 129.99, 'CAD', '2025-01-25', 'paid',    'Credit card statement - acc_cookie_cc'),
  ('inv-cb-008', 'cust-cookie',    'Cookie Malhotra',        NULL, 220.00, 'CAD', '2025-02-28', 'open',    'Investing platform fee - acc_cookie_investing'),
  ('inv-cb-009', 'cust-cashmere',  'Cashmere WiFi Malhotra', NULL,  74.25, 'CAD', '2025-02-20', 'open',    'Credit card statement - acc_cashmere_cc')
ON CONFLICT (external_id) DO NOTHING;
