##############################################################################
#
#    GNU Condo: The Free Management Condominium System
#    Copyright (C) 2016- M. Alonso <port02.server@gmail.com>
#
#
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU General Public License as published by
#    the Free Software Foundation, either version 3 of the License, or
#    (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU General Public License for more details.
#
#    You should have received a copy of the GNU General Public License
#    along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
##############################################################################

from sql import Desc, Asc

from trytond.pool import Pool
from trytond.report import Report
from trytond.transaction import Transaction

__all__ = ['PaymentDescriptionList']


class PaymentDescriptionList(Report):
    __name__ = 'condo.payment_description_list'

    @classmethod
    def get_context(cls, records, data):
        report_context = super(PaymentDescriptionList, cls).get_context(records, data)

        pool = Pool()
        cursor = Transaction().cursor

        Company = pool.get('company.company')

        table1 = pool.get('condo.party').__table__()
        table2 = pool.get('condo.unit').__table__()

        preport = []

        for r in records:
            companies = Company.search_read([
                        'OR', [
                                ('id', '=', r.company.id),
                            ],[
                                ('parent', 'child_of', r.company.id),
                            ],
                    ], order=[('party.name', 'ASC')],
                    fields_names=['id', 'party.name'])

            report = []

            for c in companies:
                cursor.execute(*table1.join(table2,
                                    condition=table1.unit == table2.id).select(
                                        table2.name, table1.role,
                                        where=((table1.sepa_mandate != None) &
                                              (table1.active == True) &
                                              (table2.company == c['id'])),
                                        order_by=(Asc(table2.name), Asc(table1.role))))
                item = {
                    'company':   c['party.name'],
                    'units':     cursor.dictfetchall()
                    }
                if len(item['units']):
                    report.append(item)

            if len(report):
                item = {
                    'reference': r.reference,
                    'condo': report
                    }
                preport.append(item)

        report_context['pgroups'] = preport

        return report_context
