-- Copyright (c) 2026 IndyKite
-- ERP invoices seed. One table for BOTH usecases: the AuthZEN search filter
-- naturally selects only the active usecase's rows (invoice external_ids
-- match the Invoice nodes provisioned in each project's knowledge graph).

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

-- SecureHome Insurance (inv-hi-*): annual premium invoices, one per
-- household policy; amounts/policy numbers mirror the HomeInsurance nodes.
INSERT INTO invoices VALUES
  ('inv-hi-001', 'james',     'James Mitchell',      'HI-2024-001', 1850.00, 'USD', '2025-01-15', 'open',    'Annual premium - home policy HI-2024-001 (123 Oak Avenue)'),
  ('inv-hi-002', 'adult-011', 'Daniel Moore',        'HI-2024-002', 1420.00, 'USD', '2025-02-01', 'open',    'Annual premium - home policy HI-2024-002'),
  ('inv-hi-003', 'adult-003', 'Michael Williams',    'HI-2024-003', 2650.00, 'USD', '2025-03-10', 'open',    'Annual premium - home policy HI-2024-003'),
  ('inv-hi-004', 'adult-013', 'Matthew White',       'HI-2024-004',  980.00, 'USD', '2025-01-20', 'paid',    'Annual premium - home policy HI-2024-004'),
  ('inv-hi-005', 'adult-005', 'David Garcia',        'HI-2024-005', 1150.00, 'USD', '2025-04-05', 'open',    'Annual premium - home policy HI-2024-005'),
  ('inv-hi-006', 'adult-015', 'Andrew Clark',        'HI-2024-006', 1780.00, 'USD', '2025-02-15', 'overdue', 'Annual premium - home policy HI-2024-006'),
  ('inv-hi-007', 'adult-007', 'Robert Anderson',     'HI-2024-007', 1320.00, 'USD', '2025-05-01', 'open',    'Annual premium - home policy HI-2024-007'),
  ('inv-hi-008', 'adult-017', 'Joshua Robinson',     'HI-2024-008', 2180.00, 'USD', '2025-03-20', 'paid',    'Annual premium - home policy HI-2024-008'),
  ('inv-hi-009', 'adult-009', 'Christopher Thomas',  'HI-2024-009', 2450.00, 'USD', '2025-06-10', 'open',    'Annual premium - home policy HI-2024-009'),
  ('inv-hi-010', 'adult-019', 'Kevin Hall',          'HI-2024-010', 1580.00, 'USD', '2025-04-15', 'open',    'Annual premium - home policy HI-2024-010')
ON CONFLICT (external_id) DO NOTHING;

-- CanBank (inv-cb-*): account fee/statement invoices for the five Customers.
INSERT INTO invoices VALUES
  ('inv-cb-001', 'alison',  'Alice Martin',   NULL,  14.50, 'CAD', '2025-01-31', 'open',    'Chequing account monthly fee - acc_alison_chequing'),
  ('inv-cb-002', 'alison',  'Alice Martin',   NULL, 923.10, 'CAD', '2025-02-01', 'open',    'Mortgage interest installment - acc_alison_mortgage'),
  ('inv-cb-003', 'bob',     'Bob Vance',      NULL,  85.00, 'CAD', '2025-01-15', 'paid',    '401k management fee Q4 - acc_bob_401k'),
  ('inv-cb-004', 'bob',     'Bob Vance',      NULL,  85.00, 'CAD', '2025-04-15', 'open',    '401k management fee Q1 - acc_bob_401k'),
  ('inv-cb-005', 'charlie', 'Charlie Day',    NULL, 312.40, 'CAD', '2025-02-10', 'overdue', 'Personal loan installment - acc_charlie_loan'),
  ('inv-cb-006', 'charlie', 'Charlie Day',    NULL, 312.40, 'CAD', '2025-03-10', 'open',    'Personal loan installment - acc_charlie_loan'),
  ('inv-cb-007', 'rebecca', 'Rebecca Welton', NULL, 129.99, 'CAD', '2025-01-25', 'paid',    'Credit card statement - acc_rebecca_cc'),
  ('inv-cb-008', 'rebecca', 'Rebecca Welton', NULL, 220.00, 'CAD', '2025-02-28', 'open',    'Investing platform fee - acc_rebecca_investing'),
  ('inv-cb-009', 'ted',     'Ted Lasso',      NULL,  74.25, 'CAD', '2025-02-20', 'open',    'Credit card statement - acc_ted_cc')
ON CONFLICT (external_id) DO NOTHING;
