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


from trytond.model import fields
from trytond.pool import Pool, PoolMeta
from trytond.pyson import Eval, If, Bool
from trytond.transaction import Transaction


__all__ = ['CondoParty', 'Unit']


class CondoParty(metaclass=PoolMeta):
    __name__ = 'condo.party'
    mandate = fields.Many2One(
        'condo.payment.sepa.mandate',
        'Mandate',
        help="SEPA Mandate of this party for the unit",
        depends=['company', 'state'],
        domain=[If(Bool(Eval('company')), [('company', '=', Eval('company'))], []), ('state', 'not in', ['canceled'])],
        ondelete='SET NULL',
    )

    @classmethod
    def validate(cls, condoparties):
        super(CondoParty, cls).validate(condoparties)
        for condoparty in condoparties:
            condoparty.unique_role_and_has_mandate()

    def unique_role_and_has_mandate(self):
        if self.mandate:
            condoparties = Pool().get('condo.party').__table__()
            cursor = Transaction().connection.cursor()

            cursor.execute(
                *condoparties.select(
                    condoparties.id,
                    where=(condoparties.unit == self.unit.id)
                    & (condoparties.role == self.role)
                    & (condoparties.mandate != None),
                )
            )

            ids = [ids for (ids,) in cursor.fetchall()]
            if len(ids) > 1:
                self.raise_user_error("Cant be two or more parties with mandates and the same role!")


class Unit(metaclass=PoolMeta):
    __name__ = 'condo.unit'
    payments = fields.One2Many('condo.payment', 'unit', 'Payments')
