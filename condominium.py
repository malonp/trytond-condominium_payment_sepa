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


from trytond.model import fields, ModelView
from trytond.pool import Pool, PoolMeta
from trytond.pyson import Eval, If, Bool
from trytond.transaction import Transaction
from trytond.wizard import Wizard, StateTransition, StateView, Button


__all__ = ['CondoParty', 'Unit',
           'CheckCondoMandatesList', 'CheckCondoMandates',
          ]


class CondoParty(metaclass=PoolMeta):
    __name__ = 'condo.party'
    sepa_mandate = fields.Many2One('condo.payment.sepa.mandate', 'Mandate',
        help="SEPA Mandate of this party for the unit",
        depends=['company'],
        domain=[If(Bool(Eval('company')),
                     [
                         ('company', '=', Eval('company')),
                     ],[]),
                ('state', 'not in', ['canceled']),
               ],
        ondelete='SET NULL',
        )

    @classmethod
    def validate(cls, condoparties):
        super(CondoParty, cls).validate(condoparties)
        for condoparty in condoparties:
            condoparty.unique_role_and_has_mandate()

    def unique_role_and_has_mandate(self):
        if self.sepa_mandate:
            condoparties = Pool().get('condo.party').__table__()
            cursor = Transaction().connection.cursor()

            cursor.execute(*condoparties.select(
                                 condoparties.id,
                                 where=(condoparties.unit == self.unit.id) &
                                       (condoparties.role == self.role) &
                                       (condoparties.sepa_mandate != None)))

            ids = [ids for (ids,) in cursor.fetchall()]
            if len(ids)>1:
                self.raise_user_error(
                    "Cant be two or more parties with mandates and the same role!")


class Unit(metaclass=PoolMeta):
    __name__ = 'condo.unit'
    payments = fields.One2Many( 'condo.payment', 'unit', 'Payments')


class CheckCondoMandatesList(ModelView):
    'Check Mandates List'
    __name__ = 'condo.check_mandates.result'
    mandates = fields.Many2Many('condo.payment.sepa.mandate', None, None,
        'Mandates not used', readonly=True)
    units = fields.Many2Many('condo.unit', None, None,
        'Units without mandates', readonly=True)


class CheckCondoMandates(Wizard):
    'Check Mandates List'
    __name__ = 'condo.check_mandates'
    start_state = 'check'

    check = StateTransition()
    result = StateView('condo.check_mandates.result',
        'condominium_payment_sepa.check_mandates_result', [
            Button('OK', 'end', 'tryton-ok', True),
            ])

    def transition_check(self):

        pool = Pool()
        CondoUnit = pool.get('condo.unit')
        CondoMandate = pool.get('condo.payment.sepa.mandate')

        mandates = CondoMandate.search_read([
                        'AND', [
                                'OR', [
                                        ('company', 'in', Transaction().context.get('active_ids')),
                                    ],[
                                        ('company.parent', 'child_of', Transaction().context.get('active_ids')),
                                    ]
                            ],[
                                ('state', 'not in', ('canceled',)),
                                ('condoparties', '=', None),
                            ],
                ], fields_names=['id'])

        units = CondoUnit.search_read([
                        'OR', [
                                ('company', 'in', Transaction().context.get('active_ids')),
                            ],[
                                ('company.parent', 'child_of', Transaction().context.get('active_ids')),
                            ],
                ], fields_names=['id'])

        units_with_mandate = CondoUnit.search_read([
                        'AND', [
                                'OR', [
                                        ('company', 'in', Transaction().context.get('active_ids')),
                                    ],[
                                        ('company.parent', 'child_of', Transaction().context.get('active_ids')),
                                    ]
                            ],[
                                ('parties.sepa_mandate', '!=', None),
                            ],
                ], fields_names=['id'])

        self.result.mandates = [r['id'] for r in mandates]
        self.result.units = [r['id'] for r in units if r not in units_with_mandate]
        return 'result'

    def default_result(self, fields):
        return {
            'mandates': [p.id for p in self.result.mandates],
            'units': [p.id for p in self.result.units],
            }
