from odoo import api, models


class SaleOrderLine(models.Model):
    _inherit = "sale.order.line"

    @api.depends("product_id", "product_uom", "product_uom_qty")
    def _compute_pricelist_item_id(self):
        # Compute the applicable pricelist item for each sale order line.
        # For rules using "Global - Ancestor Product Category":
        # - Compute cumulative product quantities per category in the order.
        # - Evaluate whether total quantity of descendant products meets min_quantity.
        # - Select the valid rule based on sequence (ASC) and discount (DESC).
        # Sale order data is cached per order to avoid redundant calculations.
        sale_data = {}
        product_categ_env = self.env["product.category"]
        for line in self:
            if line.order_id not in sale_data:
                sale_data[line.order_id] = line.order_id._get_cummulative_quantity()
            qty_data = sale_data[line.order_id]
            # First run the normal pricelist logic
            super(
                SaleOrderLine,
                line.with_context(pricelist_global_cummulative_quantity=qty_data),
            )._compute_pricelist_item_id()

            # Check if there are any global-ancestor-category rules applicable
            valid_items = []
            pricelist_items = line.order_id.pricelist_id.item_ids.filtered(
                lambda rule: rule.applied_on == "3_3_global_product_ancestor_category"
                and rule.ancestor_product_category_id
            )

            for rule in pricelist_items:
                ancestor_product_categ = rule.ancestor_product_category_id
                categories = set(
                    product_categ_env.search(
                        [("id", "child_of", ancestor_product_categ.id)]
                    ).ids
                )

                if line.product_id.categ_id.id not in categories:
                    continue

                total_qty = sum(
                    qty
                    for categ, qty in qty_data["by_categ"].items()
                    if categ.id in categories
                )

                if total_qty >= rule.min_quantity:
                    valid_items.append(rule)

            # If there's any valid global-ancestor rule, compare it with the current one
            if valid_items:
                valid_items.sort(key=lambda r: -r.percent_price)
                best_rule = valid_items[0]
                # Overwrite only if it's a better match than the current rule has best
                # discount.
                if (
                    not line.pricelist_item_id
                    or line.pricelist_item_id.applied_on
                    == "3_3_global_product_ancestor_category"
                    or (
                        best_rule.percent_price > line.pricelist_item_id.percent_price
                        and best_rule.applied_on
                        == "3_3_global_product_ancestor_category"
                )):
                    line.pricelist_item_id = best_rule
