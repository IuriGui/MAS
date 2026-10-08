SYSTEM DESCRIPTION
A web-based ERP for a mid-sized Brazilian distribution company (about 150 employees,
2 warehouses, around 8,000 SKUs). It replaces a set of spreadsheets and a legacy desktop
system. Main users are sales reps, warehouse staff, purchasing buyers, and the finance team.
Expected load: up to 200 concurrent users, with peaks at the end of the month
when financial closing happens.

MODULES
1. Users: user accounts, authentication, password lifecycle, roles and access control.
2. Customers: registry of customers and suppliers (companies and individuals), contacts,
   addresses, and credit limit.
3. Catalog: products, categories, units of measure, and price lists.
4. Inventory: stock balances per warehouse, stock movements, transfers between
   warehouses, and stock adjustments.
5. Sales: quotes, sales orders, order approval, stock reservation, and order fulfillment.
6. Purchasing: purchase requests, purchase orders, supplier quotations, and goods receipt.
7. Finance: accounts receivable, accounts payable, payments, and monthly closing.
8. Audit: central record of business actions (who did what, and when) and
   consultation of those records.

CONSTRAINTS
- Preferred stack: Sringboot for backend, frontend open. Open to other choices if justified.
- Hosting: AWS, single region (Brazil).
- Compliance: LGPD (Brazilian data protection law). Financial records must be kept
  for at least 5 years.
- The system starts as a single-company deployment, but the owner may sell it
  as a SaaS to other companies in the future.
- Users access the system only through a web browser (desktop and tablet).
- Integration with the tax authority (NF-e invoice issuing) is planned for a later
  phase and is not part of the MVP.
