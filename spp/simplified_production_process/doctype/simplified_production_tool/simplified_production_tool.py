# -*- coding: utf-8 -*-
# Copyright (c) 2017, o10c and contributors
# For license information, please see license.txt


from __future__ import unicode_literals
import frappe
from frappe.utils import cstr, flt, cint, nowdate, nowtime, add_days, comma_and

from frappe import msgprint, _

from frappe.model.document import Document
from erpnext.manufacturing.doctype.bom.bom import validate_bom_no
from erpnext.manufacturing.doctype.work_order.work_order import get_item_details
from erpnext.stock.stock_ledger import get_previous_sle, NegativeStockError


class SimplifiedProductionTool(Document):

    def clear_table(self, table_name):
        self.set(table_name, [])

    def validate_company(self):
        if not self.company:
            frappe.throw(_("Please enter Company"))

    @frappe.whitelist()
    def get_open_sales_orders(self):
        """ Pull sales orders  which are pending to deliver based on criteria selected"""
        so_filter = item_filter = ""
        if self.from_date:
            so_filter += " and so.transaction_date >= %(from_date)s"
        if self.to_date:
            so_filter += " and so.transaction_date <= %(to_date)s"
        if self.customer:
            so_filter += " and so.customer = %(customer)s"
        if self.project:
            so_filter += " and so.project = %(project)s"

        if self.fg_item:
            item_filter += " and so_item.item_code = %(item)s"

        open_so = frappe.db.sql("""
			select distinct so.name, so.transaction_date, so.customer, so.base_grand_total
			from `tabSales Order` so, `tabSales Order Item` so_item
			where so_item.parent = so.name
				and so.docstatus = 1 and so.status not in ("Stopped", "Closed")
				and so.company = %(company)s
				and so_item.qty > so_item.delivered_qty {0} {1}
				and (exists (select name from `tabBOM` bom where bom.item=so_item.item_code
						and bom.is_active = 1)
					or exists (select name from `tabPacked Item` pi
						where pi.parent = so.name and pi.parent_item = so_item.item_code
							and exists (select name from `tabBOM` bom where bom.item=pi.item_code
								and bom.is_active = 1)))
			""".format(so_filter, item_filter), {
            "from_date": self.from_date,
            "to_date": self.to_date,
            "customer": self.customer,
            "project": self.project,
            "item": self.fg_item,
            "company": self.company
        }, as_dict=1)

        self.add_so_in_table(open_so)

    def add_so_in_table(self, open_so):
        """ Add sales orders in the table"""
        # self.clear_table("sales_orders")
        # so_list = []
        so_list = [d.sales_order for d in self.get('sales_orders') if d.sales_order]
        for r in open_so:
            if cstr(r['name']) not in so_list:
                pp_so = self.append('sales_orders', {})
                pp_so.sales_order = r['name']
                pp_so.sales_order_date = cstr(r['transaction_date'])
                pp_so.customer = cstr(r['customer'])
                pp_so.grand_total = flt(r['base_grand_total'])
                
    def add_mr_in_table(self, pending_mr):
        self.clear_table("material_requests")
        mr_list = []
        for r in pending_mr:
            if cstr(r['name']) not in mr_list:
                mr = self.append('material_requests', {})
                mr.material_request = r['name']
                mr.material_request_date = cstr(r['transaction_date'])


    @frappe.whitelist()
    def get_pending_material_requests(self):
        """ Pull Material Requests that are pending based on criteria selected"""
        mr_filter = item_filter = ""
        if self.from_date:
            mr_filter += " and mr.transaction_date >= %(from_date)s"
        if self.to_date:
            mr_filter += " and mr.transaction_date <= %(to_date)s"

        # Check if warehouse attribute exists and is set
        if hasattr(self, 'warehouse') and self.warehouse:
            mr_filter += " and mr_item.warehouse = %(warehouse)s"

        if self.fg_item:
            item_filter += " and mr_item.item_code = %(item)s"

        pending_mr = frappe.db.sql("""
                select distinct mr.name, mr.transaction_date
                from `tabMaterial Request` mr, `tabMaterial Request Item` mr_item
                where mr_item.parent = mr.name
                    and mr.material_request_type = "Manufacture"
                    and mr.docstatus = 1
                    and mr.status = "Pending"
                    and mr_item.qty > ifnull(mr_item.ordered_qty,0) {0} {1}
                    and (exists (select name from `tabBOM` bom where bom.item=mr_item.item_code
                        and bom.is_active = 1))
                """.format(mr_filter, item_filter), {
            "from_date": self.from_date,
            "to_date": self.to_date,
            "warehouse": getattr(self, 'warehouse', None),
            # Using getattr to handle the case where warehouse might not be set
            "item": self.fg_item
        }, as_dict=1)
        self.add_mr_in_table(pending_mr)


    @frappe.whitelist()
    def get_items(self):
        if self.get_items_from == "Sales Order":
            self.get_so_items()
        elif self.get_items_from == "Material Request":
            self.get_mr_items()

    def get_so_items(self):
        so_list = [d.sales_order for d in self.get('sales_orders') if d.sales_order]
        if not so_list:
            msgprint(_("Please enter Sales Orders in the above table"))
            return []

        item_condition = ""
        if self.fg_item:
            item_condition = ' and so_item.item_code = "{0}"'.format(frappe.db.escape(self.fg_item))

        items = frappe.db.sql("""select distinct parent, item_code, warehouse,
			(qty - delivered_qty)*conversion_factor as pending_qty
			from `tabSales Order Item` so_item
			where parent in (%s) and docstatus = 1 and qty > delivered_qty
			and exists (select name from `tabBOM` bom where bom.item=so_item.item_code
					and bom.is_active = 1) %s""" % \
                              (", ".join(["%s"] * len(so_list)), item_condition), tuple(so_list), as_dict=1)

        if self.fg_item:
            item_condition = ' and pi.item_code = "{0}"'.format(frappe.db.escape(self.fg_item))

        packed_items = frappe.db.sql("""select distinct pi.parent, pi.item_code, pi.warehouse as warehouse,
			(((so_item.qty - so_item.delivered_qty) * pi.qty) / so_item.qty)
				as pending_qty
			from `tabSales Order Item` so_item, `tabPacked Item` pi
			where so_item.parent = pi.parent and so_item.docstatus = 1
			and pi.parent_item = so_item.item_code
			and so_item.parent in (%s) and so_item.qty > so_item.delivered_qty
			and exists (select name from `tabBOM` bom where bom.item=pi.item_code
					and bom.is_active = 1) %s""" % \
                                     (", ".join(["%s"] * len(so_list)), item_condition), tuple(so_list), as_dict=1)

        self.add_items(items + packed_items)

    def get_mr_items(self):
        mr_list = [d.material_request for d in self.get('material_requests') if d.material_request]
        if not mr_list:
            msgprint(_("Please enter Material Requests in the above table"))
            return []

        item_condition = ""
        if self.fg_item:
            item_condition = ' and mr_item.item_code = "' + frappe.db.escape(self.fg_item, percent=False) + '"'

        items = frappe.db.sql("""select distinct parent, name, item_code, warehouse,
			(qty - ordered_qty) as pending_qty
			from `tabMaterial Request Item` mr_item
			where parent in (%s) and docstatus = 1 and qty > ordered_qty
			and exists (select name from `tabBOM` bom where bom.item=mr_item.item_code
				and bom.is_active = 1) %s""" % \
                              (", ".join(["%s"] * len(mr_list)), item_condition), tuple(mr_list), as_dict=1)

        self.add_items(items)

    def add_items(self, items):
        self.clear_table("items")
        for p in items:
            item_details = get_item_details(p['item_code'])
            pi = self.append('items', {})
            pi.warehouse = p['warehouse']
            pi.item_code = p['item_code']
            # frappe today
            pi.plannad_start_date = frappe.utils.nowdate()
            pi.description = item_details and item_details.description or ''
            pi.stock_uom = item_details and item_details.stock_uom or ''
            pi.bom_no = item_details and item_details.bom_no or ''
            pi.planned_qty = flt(p['pending_qty'])
            pi.pending_qty = flt(p['pending_qty'])

            if self.get_items_from == "Sales Order":
                pi.sales_order = p['parent']
            elif self.get_items_from == "Material Request":
                pi.material_request = p['parent']
                pi.material_request_item = p['name']

    def validate_data(self):
        self.validate_company()
        for d in self.get('items'):
            if not d.bom_no:
                frappe.throw(_("Please select BOM for Item in Row {0}".format(d.idx)))
            else:
                validate_bom_no(d.item_code, d.bom_no)

            if not flt(d.planned_qty):
                frappe.throw(_("Please enter Planned Qty for Item {0} at row {1}").format(d.item_code, d.idx))

    @frappe.whitelist()
    def raise_stock_entries(self):
        """It will raise a stock entries of purpose manufacture for all distinct FG items"""

        self.validate_data()

        from erpnext.utilities.transaction_base import validate_uom_is_integer
        validate_uom_is_integer(self, "stock_uom", "planned_qty")

        # frappe.flags.mute_messages = False

        items = self.get_manufacture_items()

        allow_negative_stock = cint(frappe.db.get_value("Stock Settings", None, "allow_negative_stock"))

        se_list = []

        for key in items:
            i = items[key]
            se = self.make_stock_entry(items[key])
            print(se)
            if se:

                # check quantities in stock for each BOM items before insert and submit
                for d in se.items:
                    previous_sle = get_previous_sle({
                        "item_code": d.item_code,
                        "warehouse": d.s_warehouse or d.t_warehouse,
                        "posting_date": nowdate(),
                        "posting_time": nowtime()
                    })

                    # get actual stock at source warehouse
                    d.actual_qty = previous_sle.get("qty_after_transaction") or 0

                    # validate qty before submit
                    if d.s_warehouse and not allow_negative_stock and d.actual_qty < d.transfer_qty:
                        frappe.throw(_("For manufacturing item {0} you need {3} x {2} in warehouse {1}").format(
                            i['manufacture_item'],
                            frappe.bold(d.s_warehouse), frappe.bold(d.item_code), frappe.bold(d.transfer_qty))
                                     + '<br>' + _("Available qty is {0}, you need {1}").format(
                            frappe.bold(d.actual_qty), frappe.bold(d.transfer_qty)),
                                     NegativeStockError, title=_('Insufficient Stock'))

                se_list.append(se)

        if se_list:
            for se in se_list:
                se.insert()
                se.submit()
            se_list = ["""<a href="#Form/Stock Entry/%s" target="_blank">%s</a>""" % (p.name, p.title) for p in se_list]
            msgprint(_("Created {0} stock entries:<br />{1}").format(len(se_list), "<br/>".join(se_list)))
            for material in self.material_requests:                
                material_request = frappe.get_doc("Material Request", material.material_request)
                # material_request.status = "Issued"
                frappe.db.set_value("Material Request", material_request.name, "status", "Issued")
                frappe.db.commit()
        else:
            msgprint(_("No Stock Entry created"))

    def get_manufacture_items(self):
        item_dict = {}
        for d in self.get("items"):
            item_details = {
                "manufacture_item"	: d.item_code,
                "sales_order"			: d.sales_order,
                "material_request"		: d.material_request,
                "material_request_item"	: d.material_request_item,
                "bom_no"				: d.bom_no,
                "description"			: d.description,
                "stock_uom"				: d.stock_uom,
                "company"				: self.company,
                "from_warehouse"			: "",
                "to_warehouse"			: self.warehouse,
                "project"				: frappe.db.get_value("Sales Order", d.sales_order, "project")
            }

            """ Club similar BOM and item for processing in case of Sales Orders """
            if self.get_items_from == "Material Request":
                item_details.update({
                    "fg_completed_qty": d.planned_qty
                })
                item_dict[(d.item_code, d.material_request_item, d.warehouse)] = item_details

            else:
                item_details.update({
                    "fg_completed_qty" :flt(item_dict.get((d.item_code, d.sales_order, d.warehouse) ,{})
                                           .get("fg_completed_qty")) + flt(d.planned_qty)
                })
                item_dict[(d.item_code, d.sales_order, d.warehouse)] = item_details

        return item_dict

    def make_stock_entry(self, item_dict):
        """Create a stock entry with purpose of manufacturing."""
        from erpnext.manufacturing.doctype.work_order.work_order import  get_default_warehouse

        warehouse = get_default_warehouse()
        se = frappe.new_doc("Stock Entry")
        se.update(item_dict)
        
        se.purpose = "Manufacture"
        se.stock_entry_type = "Manufacture"
        se.additional_costs = [
            frappe.get_doc({
                "doctype": "Landed Cost Taxes and Charges",
                "expense_account": "5103 - Freight - MG",
                "description": "f",
                "amount": 0.0
                })
        ]
        se.remarks = "bulk"
        se.status = "submitted" # submitted or "Draft"
        se.from_bom = 1
        se.use_multi_level_bom = 1
        se.title = "Manufacture %s x %s" % (se.fg_completed_qty ,se.manufacture_item)
        if (se.sales_order):
            se.title += " for order %s" % (se.sales_order)
        if warehouse:
            se.from_warehouse = self.warehouse
        if not se.to_warehouse:
            se.to_warehouse = self.warehouse

        se.get_items()

        return se







    def get_so_wise_planned_qty(self):
        """
            bom_dict {
                bom_no: ['sales_order', 'qty']
            }
        """
        bom_dict = {}
        for d in self.get("items"):
            if self.get_items_from == "Material Request":
                bom_dict.setdefault(d.bom_no, []).append([d.material_request_item, flt(d.planned_qty)])
            else:
                bom_dict.setdefault(d.bom_no, []).append([d.sales_order, flt(d.planned_qty)])
        return bom_dict

    def download_raw_materials(self):
        """ Create csv data for required raw material to produce finished goods"""
        self.validate_data()
        bom_dict = self.get_so_wise_planned_qty()
        self.get_raw_materials(bom_dict)
        return self.get_csv()

    def get_raw_materials(self, bom_dict ,non_stock_item=0):
        """ Get raw materials considering sub-assembly items
            {
                "item_code": [qty_required, description, stock_uom, min_order_qty]
            }
        """
        item_list = []

        for bom, so_wise_qty in bom_dict.items():
            bom_wise_item_details = {}
            if self.use_multi_level_bom and self.only_raw_materials and self.include_subcontracted:
                # get all raw materials with sub assembly childs
                # Did not use qty_consumed_per_unit in the query, as it leads to rounding loss
                for d in frappe.db.sql("""select fb.item_code,
					ifnull(sum(fb.stock_qty/ifnull(bom.quantity, 1)), 0) as qty,
					fb.description, fb.stock_uom, item.min_order_qty
					from `tabBOM Explosion Item` fb, `tabBOM` bom, `tabItem` item
					where bom.name = fb.parent and item.name = fb.item_code
					and (item.is_sub_contracted_item = 0 or ifnull(item.default_bom, "")="")
					""" + ("and item.is_stock_item = 1" ,"")[non_stock_item] + """
					and fb.docstatus<2 and bom.name=%(bom)s
					group by fb.item_code, fb.stock_uom""", {"bom" :bom}, as_dict=1):
                    bom_wise_item_details.setdefault(d.item_code, d)
            else:
                # Get all raw materials considering SA items as raw materials,
                # so no childs of SA items
                bom_wise_item_details = self.get_subitems(bom_wise_item_details, bom ,1, \
                                                          self.use_multi_level_bom ,self.only_raw_materials, self.include_subcontracted ,non_stock_item)

            for item, item_details in bom_wise_item_details.items():
                for so_qty in so_wise_qty:
                    item_list.append([item, flt(item_details.qty) * so_qty[1], item_details.description,
                                      item_details.stock_uom, item_details.min_order_qty, so_qty[0]])

        self.make_items_dict(item_list)

    def get_subitems(self ,bom_wise_item_details, bom, parent_qty, include_sublevel, only_raw, supply_subs
                     ,non_stock_item=0):
        items = frappe.db.sql("""
			SELECT
				bom_item.item_code,
				default_material_request_type,
				ifnull(%(parent_qty)s * sum(bom_item.stock_qty/ifnull(bom.quantity, 1)), 0) as qty,
				item.is_sub_contracted_item as is_sub_contracted,
				item.default_bom as default_bom,
				bom_item.description as description,
				bom_item.stock_uom as stock_uom,
				item.min_order_qty as min_order_qty
			FROM
				`tabBOM Item` bom_item,
				`tabBOM` bom,
				tabItem item
			where
				bom.name = bom_item.parent
				and bom.name = %(bom)s
				and bom_item.docstatus < 2
				and bom_item.item_code = item.name
			""" + ("and item.is_stock_item = 1", "")[non_stock_item] + """
			group by bom_item.item_code""", {"bom": bom, "parent_qty": parent_qty}, as_dict=1)

        for d in items:
            if ((d.default_material_request_type == "Purchase"
                 and not (d.is_sub_contracted and only_raw and include_sublevel))
                    or (d.default_material_request_type == "Manufacture" and not only_raw)):

                if d.item_code in bom_wise_item_details:
                    bom_wise_item_details[d.item_code].qty = bom_wise_item_details[d.item_code].qty + d.qty
                else:
                    bom_wise_item_details[d.item_code] = d

            if include_sublevel:
                if ((d.default_material_request_type == "Purchase" and d.is_sub_contracted and supply_subs)
                        or (d.default_material_request_type == "Manufacture")):

                    my_qty = 0
                    projected_qty = self.get_item_projected_qty(d.item_code)

                    if self.create_material_requests_for_all_required_qty:
                        my_qty = d.qty
                    elif (bom_wise_item_details[d.item_code].qty - d.qty) < projected_qty:
                        my_qty = bom_wise_item_details[d.item_code].qty - projected_qty
                    else:
                        my_qty = d.qty

                    if my_qty > 0:
                        self.get_subitems(bom_wise_item_details,
                                          d.default_bom, my_qty, include_sublevel, only_raw, supply_subs)

        return bom_wise_item_details

    def make_items_dict(self, item_list):
        for i in item_list:
            self.item_dict.setdefault(i[0], []).append([flt(i[1]), i[2], i[3], i[4], i[5]])

    def get_csv(self):
        item_list = [['Item Code', 'Description', 'Stock UOM', 'Required Qty', 'Warehouse',
                      'Quantity Requested for Purchase', 'Ordered Qty', 'Actual Qty']]
        for item in self.item_dict:
            total_qty = sum([flt(d[0]) for d in self.item_dict[item]])
            item_list.append([item, self.item_dict[item][0][1], self.item_dict[item][0][2], total_qty])
            item_qty = frappe.db.sql("""select warehouse, indented_qty, ordered_qty, actual_qty
				from `tabBin` where item_code = %s""", item, as_dict=1)

            i_qty, o_qty, a_qty = 0, 0, 0
            for w in item_qty:
                i_qty, o_qty, a_qty = i_qty + flt(w.indented_qty), o_qty + \
                                      flt(w.ordered_qty), a_qty + flt(w.actual_qty)

                item_list.append(['', '', '', '', w.warehouse, flt(w.indented_qty),
                                  flt(w.ordered_qty), flt(w.actual_qty)])
            if item_qty:
                item_list.append(['', '', '', '', 'Total', i_qty, o_qty, a_qty])
            else:
                item_list.append(['', '', '', '', 'Total', 0, 0, 0])

        return item_list

    def raise_material_requests(self):
        """
            Raise Material Request if projected qty is less than qty required
            Requested qty should be shortage qty considering minimum order qty
        """
        self.validate_data()
        if not self.purchase_request_for_warehouse:
            frappe.throw(_("Please enter Warehouse for which Material Request will be raised"))

        bom_dict = self.get_so_wise_planned_qty()
        self.get_raw_materials(bom_dict ,self.create_material_requests_non_stock_request)

        if self.item_dict:
            self.create_material_request()

    def get_requested_items(self):
        items_to_be_requested = frappe._dict()

        if not self.create_material_requests_for_all_required_qty:
            item_projected_qty = self.get_projected_qty()

        for item, so_item_qty in self.item_dict.items():
            total_qty = sum([flt(d[0]) for d in so_item_qty])
            requested_qty = 0

            if self.create_material_requests_for_all_required_qty:
                requested_qty = total_qty
            elif total_qty > item_projected_qty.get(item, 0):
                # shortage
                requested_qty = total_qty - flt(item_projected_qty.get(item))
            # consider minimum order qty

            if requested_qty and requested_qty < flt(so_item_qty[0][3]):
                requested_qty = flt(so_item_qty[0][3])

            # distribute requested qty SO wise
            for item_details in so_item_qty:
                if requested_qty:
                    sales_order = item_details[4] or "No Sales Order"
                    if self.get_items_from == "Material Request":
                        sales_order = "No Sales Order"
                    if requested_qty <= item_details[0]:
                        adjusted_qty = requested_qty
                    else:
                        adjusted_qty = item_details[0]

                    items_to_be_requested.setdefault(item, {}).setdefault(sales_order, 0)
                    items_to_be_requested[item][sales_order] += adjusted_qty
                    requested_qty -= adjusted_qty
                else:
                    break

            # requested qty >= total so qty, due to minimum order qty
            if requested_qty:
                items_to_be_requested.setdefault(item, {}).setdefault("No Sales Order", 0)
                items_to_be_requested[item]["No Sales Order"] += requested_qty

        return items_to_be_requested

    def get_item_projected_qty(self ,item):
        item_projected_qty = frappe.db.sql("""
			select ifnull(sum(projected_qty),0) as qty
			from `tabBin`
			where item_code = %(item_code)s and warehouse=%(warehouse)s
		""", {
            "item_code": item,
            "warehouse": self.purchase_request_for_warehouse
        }, as_dict=1)

        return item_projected_qty[0].qty

    def get_projected_qty(self):
        items = self.item_dict.keys()
        item_projected_qty = frappe.db.sql("""select item_code, sum(projected_qty)
			from `tabBin` where item_code in (%s) and warehouse=%s group by item_code""" %
                                           (", ".join(["%s" ] *len(items)), '%s'), tuple(items + [self.purchase_request_for_warehouse]))

        return dict(item_projected_qty)

    def create_material_request(self):
        items_to_be_requested = self.get_requested_items()

        material_request_list = []
        if items_to_be_requested:
            for item in items_to_be_requested:
                item_wrapper = frappe.get_doc("Item", item)
                material_request = frappe.new_doc("Material Request")
                material_request.update({
                    "transaction_date": nowdate(),
                    "status": "Draft",
                    "company": self.company,
                    "requested_by": frappe.session.user
                })
                material_request.update({"material_request_type": item_wrapper.default_material_request_type})

                for sales_order, requested_qty in items_to_be_requested[item].items():
                    material_request.append("items", {
                        "doctype": "Material Request Item",
                        "__islocal": 1,
                        "item_code": item,
                        "item_name": item_wrapper.item_name,
                        "description": item_wrapper.description,
                        "uom": item_wrapper.stock_uom,
                        "item_group": item_wrapper.item_group,
                        "brand": item_wrapper.brand,
                        "qty": requested_qty,
                        "schedule_date": add_days(nowdate(), cint(item_wrapper.lead_time_days)),
                        "warehouse": self.purchase_request_for_warehouse,
                        "sales_order": sales_order if sales_order != "No Sales Order" else None,
                        "project": frappe.db.get_value("Sales Order", sales_order, "project") \
                            if sales_order != "No Sales Order" else None
                    })

                material_request.flags.ignore_permissions = 1
                material_request.submit()
                material_request_list.append(material_request.name)

            if material_request_list:
                message = ["""<a href="#Form/Material Request/%s" target="_blank">%s</a>""" % \
                           (p, p) for p in material_request_list]
                msgprint(_("Material Requests {0} created").format(comma_and(message)))
        else:
            msgprint(_("Nothing to request"))