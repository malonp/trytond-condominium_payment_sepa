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
from trytond.pyson import Eval, Not, Bool
from trytond.tools import reduce_ids, grouped_slice
from trytond.transaction import Transaction

from . import sepadecode

EPC_COUNTRIES = list(sepadecode._countries)


__all__ = ['Bank', 'BankAccount', 'BankAccountNumber']


class Bank(metaclass=PoolMeta):
    __name__ = 'bank'
    subset = fields.Boolean(
        'ASCII ISO20022', help=('Use Unicode Character Subset defined in ISO20022 for SEPA schemes')
    )
    country_subset = fields.Many2One(
        'country.country',
        'Extended Character Set',
        domain=[('code', 'in', EPC_COUNTRIES)],
        help=('Country Extended Character Set'),
        states={'invisible': Not(Bool(Eval('subset')))},
    )

    @staticmethod
    def default_subset():
        return False


class BankAccount(metaclass=PoolMeta):
    __name__ = 'bank.account'

    @classmethod
    def validate(cls, bankaccounts):
        super(BankAccount, cls).validate(bankaccounts)
        for bankaccount in bankaccounts:
            bankaccount.validate_active()

    def validate_active(self):
        # Cancel mandates with account number of this bank account.
        if (self.id > 0) and not self.active:
            mandates = Pool().get('condo.payment.sepa.mandate').__table__()
            condoparties = Pool().get('condo.party').__table__()
            cursor = Transaction().connection.cursor()

            red_sql = reduce_ids(mandates.account_number, [x.id for x in self.numbers])
            cursor.execute(
                *mandates.select(mandates.id, mandates.identification, where=red_sql & (mandates.state != 'canceled'))
            )

            for id, identification in cursor.fetchall():
                cursor.execute(
                    *condoparties.select(
                        condoparties.id, where=(condoparties.mandate == id) & (condoparties.active == True)
                    )
                )

                ids = [ids for (ids,) in cursor.fetchall()]
                if len(ids):
                    self.raise_user_warning(
                        'warn_deactive_mandate.%d' % id,
                        'Mandate "%s" will be canceled and deactivate as mean of payment in %d unit(s)/apartment(s)!',
                        (identification, len(ids)),
                    )

                    # Use SQL to prevent double validate loop
                    cursor.execute(
                        *mandates.update(columns=[mandates.state], values=['canceled'], where=(mandates.id == id))
                    )

                    for sub_ids in grouped_slice(ids):
                        red_sql = reduce_ids(condoparties.id, sub_ids)
                        cursor.execute(
                            *condoparties.update(columns=[condoparties.mandate], values=[None], where=red_sql)
                        )


class BankAccountNumber(metaclass=PoolMeta):
    __name__ = 'bank.account.number'
    mandates = fields.One2Many('condo.payment.sepa.mandate', 'account_number', 'Mandates')
