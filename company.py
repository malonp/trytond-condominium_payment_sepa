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


from trytond.pool import PoolMeta
from trytond.model import ModelView, fields, Unique
from trytond.pyson import Eval, Bool

from stdnum.iso7064 import mod_97_10
from stdnum.eu.at_02 import is_valid, _to_base10
import stdnum.exceptions

__metaclass__ = PoolMeta
__all__ = ['Company']


class Company:
    __name__ = 'company.company'
    sepa_mandates = fields.One2Many('condo.payment.sepa.mandate', 'company',
        'SEPA Mandates')
    groups_payments = fields.One2Many('condo.payment.group', 'company', 'Condominium Payment Group',
        readonly=True)
    creditor_bussines_code = fields.Char('Creditor Bussines Code', size=3,
       help='Code used in the SEPA Creditor Identifier')
    sepa_creditor_identifier = fields.Char('SEPA Creditor Identifier', size=35)

    @classmethod
    def __setup__(cls):
        super(Company, cls).__setup__()
        t = cls.__table__()
        cls._sql_constraints += [
            ('condo_credid_uniq', Unique(t,t.sepa_creditor_identifier),
                'This sepa creditor identifier is already in use!'),
        ]
        cls._buttons.update({
                'calculate_sepa_creditor_identifier': {
                    'invisible': Bool(Eval('sepa_creditor_identifier', False)),
                    }
                })

    @staticmethod
    def default_creditor_bussines_code():
        return 'ZZZ'

    @classmethod
    def validate(cls, companies):
        super(Company, cls).validate(companies)
        for company in companies:
            company.check_sepa_creditor_identifier()

    def check_sepa_creditor_identifier(self):
        if not self.sepa_creditor_identifier:
            return
        if not is_valid(self.sepa_creditor_identifier):
            self.raise_user_error('invalid_creditor_identifier',
                (self.sepa_creditor_identifier, self.rec_name))

    @classmethod
    @ModelView.button
    def calculate_sepa_creditor_identifier(cls, companies, _save=True):
        for company in companies:
            if not company.party.vat_code:
                continue
            number = _to_base10(company.party.vat_code[:2] + '00' +
                company.creditor_bussines_code +
                company.party.vat_code[2:].upper())
            check_sum = mod_97_10.calc_check_digits(number[:-2])
            company.sepa_creditor_identifier = (company.party.vat_code[:2] + check_sum +
                company.creditor_bussines_code +
                company.party.vat_code[2:].upper())
            company.save()

    @classmethod
    def write(cls, *args):
        actions = iter(args)
        args = []
        for companies, values in zip(actions, actions):
            # Prevent raising false unique constraint
            if values.get('sepa_creditor_identifier') == '':
                values = values.copy()
                values['sepa_creditor_identifier'] = None
            args.extend((companies, values))
        super(Company, cls).write(*args)
