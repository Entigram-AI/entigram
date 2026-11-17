/* Synthetic demonstration model; all amounts are USD. */
ENTITY: SupplierInvoice {
    id String PK
    supplier_id String
    invoice_total Decimal
}
ENTITY: PurchaseOrder {
    id String PK
    supplier_id String
    invoice_total Decimal
}
