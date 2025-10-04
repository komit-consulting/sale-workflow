from odoo.addons.base.tests.common import TransactionCase
from odoo import fields


class TestPricelistAncestor(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env

        # --- UoM & Partner
        cls.uom_unit = env.ref("uom.product_uom_unit")
        cls.partner = env["res.partner"].create({"name": "Test Customer"})

        # --- Category tree:
        # Cat A
        # ├─ Cat B
        # └─ Cat C
        #    └─ Cat D
        cls.cat_a = env["product.category"].create({"name": "Cat A"})
        cls.cat_b = env["product.category"].create(
            {"name": "Cat B", "parent_id": cls.cat_a.id}
        )
        cls.cat_c = env["product.category"].create(
            {"name": "Cat C", "parent_id": cls.cat_a.id}
        )
        cls.cat_d = env["product.category"].create(
            {"name": "Cat D", "parent_id": cls.cat_c.id}
        )

        def _create_product_template(name, categ):
            tmpl = env["product.template"].create(
                {
                    "name": name,
                    "list_price": 100.0,
                    "uom_id": cls.uom_unit.id,
                    "uom_po_id": cls.uom_unit.id,
                    "categ_id": categ.id,
                    "type": "consu",
                }
            )
            return tmpl.product_variant_id

        cls.prod_a = _create_product_template("Prod A", cls.cat_a)
        cls.prod_b = _create_product_template("Prod B", cls.cat_b)
        cls.prod_c = _create_product_template("Prod C", cls.cat_c)
        cls.prod_d = _create_product_template("Prod D", cls.cat_d)

        def _create_pricelist(name):
            return env["product.pricelist"].create(
                {
                    "name": name,
                    "discount_policy": "without_discount",
                    "currency_id": env.ref("base.USD").id,
                }
            )

        def _create_pricelist_item(
            pricelist, applied_on, compute_price, percent_price, min_quantity, name,
            ancestor_product_category_id=None, product_id=None
        ):
            vals = {
                "pricelist_id": pricelist.id,
                "applied_on": applied_on,
                "compute_price": compute_price,
                "percent_price": percent_price,
                "min_quantity": min_quantity,
                "name": name,
            }
            if ancestor_product_category_id:
                vals["ancestor_product_category_id"] = ancestor_product_category_id.id
            if product_id:
                vals["product_id"] = product_id.id
            return env["product.pricelist.item"].create(vals)

        # --- PRICELIST for Test Case 1
        cls.pl_case1 = _create_pricelist("Price List 1")
        # PI0: Variant Prod A -> 30%
        cls.pi0 = _create_pricelist_item(
            cls.pl_case1, "0_product_variant", "percentage",
            30.0, 1.0, "PI0", product_id=cls.prod_a
        )
        # PI1: Ancestor Cat C -> 20% (min 5)
        cls.pi1 = _create_pricelist_item(
            cls.pl_case1, "3_3_global_product_ancestor_category",
            "percentage", 20.0,5.0, "PI1", ancestor_product_category_id=cls.cat_c
        )
        # PI2: Ancestor Cat A -> 10% (min 5)
        cls.pi2 = _create_pricelist_item(
            cls.pl_case1, "3_3_global_product_ancestor_category", "percentage", 10.0,
            5.0, "PI2", ancestor_product_category_id=cls.cat_a
        )

        # --- PRICELIST for Test Case 2
        cls.pl_case2 = _create_pricelist("Price List 2")
        # PI0: Ancestor Cat C -> 20%, seq 0
        cls.pi0p = _create_pricelist_item(
            cls.pl_case2, "3_3_global_product_ancestor_category",
            "percentage",20.0, 5.0, "PI0'", ancestor_product_category_id=cls.cat_c
        )
        # PI1: Ancestor Cat A -> 10%, seq 1
        cls.pi1p = _create_pricelist_item(
            cls.pl_case2, "3_3_global_product_ancestor_category",
            "percentage",10.0, 5.0, "PI1'", ancestor_product_category_id=cls.cat_a
        )
        # --- PRICELIST for Case 3 (tie on % and sequence; break by create_date)
        cls.pl_case3 = _create_pricelist("Price List 3")
        # Two rules on Cat A, both 15%, same sequence
        cls.pi_tie_old = _create_pricelist_item(
            cls.pl_case3, "3_3_global_product_ancestor_category",
            "percentage",15.0, 1.0, "Tie older", ancestor_product_category_id=cls.cat_a
        )
        cls.pi_tie_new = _create_pricelist_item(
            cls.pl_case3, "3_3_global_product_ancestor_category",
            "percentage",15.0, 1.0, "Tie newer", ancestor_product_category_id=cls.cat_a
        )
        # Force create_date so that pi_tie_old is older than pi_tie_new
        # Note: write to create_date via SQL for deterministic ordering
        cr = env.cr
        cr.execute(
            "UPDATE product_pricelist_item SET create_date = NOW() - interval '2 days' WHERE id = %s",
            (cls.pi_tie_old.id,),
        )
        cr.execute(
            "UPDATE product_pricelist_item SET create_date = NOW() - interval '1 day' WHERE id = %s",
            (cls.pi_tie_new.id,),
        )

    def _new_order(self, pricelist):
        return self.env["sale.order"].create(
            {"partner_id": self.partner.id, "pricelist_id": pricelist.id}
        )

    def _add_line(self, order, product, qty):
        return self.env["sale.order.line"].create(
            {
                "order_id": order.id,
                "product_id": product.id,
                "product_uom": self.uom_unit.id,
                "product_uom_qty": qty,
                "name": product.name,
                "price_unit": product.list_price,
            }
        )

    # ---------- tests ----------
    def test_case_1_best_percentage_and_ancestor_totals(self):
        """
        Case 1:
        - Rules: PI0 (Prod A 30%), PI1 (Cat C 20%, min=5), PI2 (Cat A 10%, min=5)
        - Order lines: A(2), B(2), C(4), D(2)
        - Totals: Cat A branch=10, Cat C branch=6
        Expected:
        - A → PI0 (30%)
        - B → PI2 (10%)
        - C → PI1 (20%)
        - D → PI1 (20%)
        """
        order = self._new_order(self.pl_case1)
        l1 = self._add_line(order, self.prod_a, 2)
        l2 = self._add_line(order, self.prod_b, 2)
        l3 = self._add_line(order, self.prod_c, 4)
        l4 = self._add_line(order, self.prod_d, 2)

        order.button_compute_pricelist_global_rule()

        self.assertEqual(l1.pricelist_item_id, self.pi0)
        self.assertEqual(l2.pricelist_item_id, self.pi2)
        self.assertEqual(l3.pricelist_item_id, self.pi1)
        self.assertEqual(l4.pricelist_item_id, self.pi1)

        self.assertAlmostEqual(l1.discount, 30.0)
        self.assertAlmostEqual(l2.discount, 10.0)
        self.assertAlmostEqual(l3.discount, 20.0)
        self.assertAlmostEqual(l4.discount, 20.0)

    def test_case_2_best_percentage(self):
        """
        Case 2:
        - Rules: PI0' (Cat C 20%), PI1' (Cat A 10%)
        - Order lines: A(2), B(2), C(4), D(2)
        Expected:
        - A → PI1' (10%) because Cat A branch total=10
        - B → PI1' (10%)
        - C → PI0' (20%) because Cat C branch total=6
        - D → PI0' (20%)
        """
        order = self._new_order(self.pl_case2)
        l1 = self._add_line(order, self.prod_a, 2)
        l2 = self._add_line(order, self.prod_b, 2)
        l3 = self._add_line(order, self.prod_c, 4)
        l4 = self._add_line(order, self.prod_d, 2)

        order.button_compute_pricelist_global_rule()

        self.assertEqual(l1.pricelist_item_id, self.pi1p)
        self.assertEqual(l2.pricelist_item_id, self.pi1p)
        self.assertEqual(l3.pricelist_item_id, self.pi0p)
        self.assertEqual(l4.pricelist_item_id, self.pi0p)

        self.assertAlmostEqual(l1.discount, 10.0)
        self.assertAlmostEqual(l2.discount, 10.0)
        self.assertAlmostEqual(l3.discount, 20.0)
        self.assertAlmostEqual(l4.discount, 20.0)

    def test_case_3_tie_on_percent_break_by_create_date(self):
        """
        Case 3:
        - Rules: Two Cat A rules, both 15%, same min_qty=1
          * pi_tie_old (older create_date)
          * pi_tie_new (newer create_date)
        - Order lines: B(2), C(3) → both descendants of Cat A
        Expected:
        - Both lines pick pi_tie_old (older rule wins in tie).
        """
        order = self._new_order(self.pl_case3)
        l1 = self._add_line(order, self.prod_b, 2)
        l2 = self._add_line(order, self.prod_c, 3)

        order.button_compute_pricelist_global_rule()

        self.assertEqual(l1.pricelist_item_id, self.pi_tie_old)
        self.assertEqual(l2.pricelist_item_id, self.pi_tie_old)

        self.assertAlmostEqual(l1.discount, 15.0)
        self.assertAlmostEqual(l2.discount, 15.0)
