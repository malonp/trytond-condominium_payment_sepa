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

from trytond.pool import Pool
from trytond.report import Report


__all__ = ['PaymentDescriptionList']


class PaymentDescriptionList(Report):
    __name__ = 'condo.payment_description_list'

    @classmethod
    def get_context(cls, records, data):
        report_context = super(PaymentDescriptionList, cls).get_context(records, data)

        pool = Pool()
        CondoParty = pool.get('condo.party')
        CondoUnit = pool.get('condo.unit')

        units = CondoUnit.search_read([
                    'OR', [
                            ('company', 'in', [x.company.id for x in records]),
                        ],[
                            ('company.parent', 'child_of', [x.company.id for x in records]),
                        ],
                ], fields_names=['id'])

        condoparties = CondoParty.search([
                ('unit', 'in', [ x['id'] for x in units ]),
                ('sepa_mandate', '!=', None),
                ('isactive', '=', True),
                ], order=[('unit.company', 'ASC'), ('unit.name', 'ASC')])

        report = []

        for condoparty in condoparties:
            item = {
                'name': condoparty.unit_name,
                'role': condoparty.role
                }
            report.append(item)

        report_context['records'] = report

        return report_context
