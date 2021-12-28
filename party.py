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
from trytond.tools import grouped_slice, reduce_ids
from trytond.transaction import Transaction

__all__ = ['Party', 'PartyReplace']


class Party(metaclass=PoolMeta):
    __name__ = 'party.party'
    companies = fields.One2Many('company.company', 'party', 'Companies')
    mandates = fields.One2Many('condo.payment.sepa.mandate', 'party', 'SEPA Mandates')

    @classmethod
    def validate(cls, parties):
        super(Party, cls).validate(parties)
        for party in parties:
            party.validate_mandates()

    def validate_mandates(self):
        # Cancel party's mandates on party deactivate
        if (self.id > 0) and not self.active:
            mandates = Pool().get('condo.payment.sepa.mandate').__table__()
            cursor = Transaction().connection.cursor()

            cursor.execute(
                *mandates.select(mandates.id, where=(mandates.party == self.id) & (mandates.state != 'canceled'))
            )

            ids = [ids for (ids,) in cursor.fetchall()]
            if len(ids):
                self.raise_user_warning(
                    'warn_cancel_mandates_of_party.%d' % self.id,
                    '%d mandate(s) of this party will be canceled!',
                    len(ids),
                )

                for sub_ids in grouped_slice(ids):
                    red_sql = reduce_ids(mandates.id, sub_ids)
                    # Use SQL to prevent double validate loop
                    cursor.execute(*mandates.update(columns=[mandates.state], values=['canceled'], where=red_sql))


class PartyReplace(metaclass=PoolMeta):
    __name__ = 'party.replace'

    @classmethod
    def fields_to_replace(cls):
        return super(PartyReplace, cls).fields_to_replace() + [
            ('condo.payment', 'party'),
            ('condo.payment.sepa.mandate', 'party'),
        ]
