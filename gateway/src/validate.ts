// Request body checks for POST /orders. Returns a list of
// problems; an empty list means the body is valid.

export interface OrderRequest {
  customer_id: string;
  items: { sku: string; qty: number }[];
  card_token: string;
}

const isText = (v: unknown) =>
  typeof v === "string" && v.trim().length > 0;

export function validateOrder(body: unknown): string[] {
  if (!body || typeof body !== "object" || Array.isArray(body)) {
    return ["body must be a JSON object"];
  }
  const b = body as Record<string, unknown>;
  const errors: string[] = [];
  if (!isText(b.customer_id)) {
    errors.push("customer_id must be a non-empty string");
  }
  if (!isText(b.card_token)) {
    errors.push("card_token must be a non-empty string");
  }
  if (!Array.isArray(b.items) || b.items.length === 0) {
    errors.push("items must be a non-empty array");
  } else {
    b.items.forEach((item, i) => {
      const it = item as Record<string, unknown> | null;
      if (!it || typeof it !== "object") {
        errors.push(`items[${i}] must be an object`);
        return;
      }
      if (!isText(it.sku)) {
        errors.push(`items[${i}].sku must be a non-empty string`);
      }
      if (!Number.isInteger(it.qty) || (it.qty as number) < 1) {
        errors.push(`items[${i}].qty must be an integer >= 1`);
      }
    });
  }
  return errors;
}
