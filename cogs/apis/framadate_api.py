import csv
import aiohttp
import requests
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
from io import StringIO


class FramadateAPI(object):
    """
    API permettant de creer un sondage Framadate
    """
    BASE_URL = 'https://framadate.org'
    CREATION_ENDPOINT = '/create_poll.php'
    DATE_POLL_ENDPOINT = '/create_date_poll.php'
    EXPORT_ENDPOINT = '/exportcsv.php'

    def __init__(self):
        self.session = requests.Session()

    @staticmethod
    def _check_response(response):
        """
        Gere les erreurs de requete HTTP
        :param response: Reponse HTTP
        :return:
        """
        if response.status_code != 200:
            response.raise_for_status()

    @staticmethod
    def generate_schedule(start_date, num_days):
        """
        Genere les dates du sondage en fonction de la date de debut et du nombre de jours a planifier
        :param start_date: Date de debut du sondage au format dd/mm/yyyy
        :param num_days: Nombre de jours a planifier
        :return: Liste des dates au format attendu par Framadate
        """
        start = datetime.strptime(start_date, '%d/%m/%Y')
        data = []

        weekday_slots = ["Fin d'aprem", "Soir"]
        weekend_slots = ["Après-midi", "Fin d'aprem", "Soir"]

        for day in range(num_days):
            current_date = start + timedelta(days=day)
            date_str = current_date.strftime('%d/%m/%Y')

            data.append(('days[]', date_str))

            if current_date.weekday() >= 5:
                for slot in weekend_slots:
                    data.append((f'horaires{day}[]', slot))
            else:
                for slot in weekday_slots:
                    data.append((f'horaires{day}[]', slot))

        data.append(('choixheures', 'Continuer'))
        return data

    @staticmethod
    def get_public_poll_link(html):
        """
        Recupere le lien public du sondage
        :param html: HTML de la page d'administration du sondage
        :return: Lien public du sondage
        """
        soup = BeautifulSoup(html, 'html.parser')
        poll_link_tag = soup.find('input', {'id': 'public-link'})
        if poll_link_tag:
            return poll_link_tag.get('value')

    @staticmethod
    def get_control_token(html):
        """
        Recupere le token de controle du sondage afin de permettre de tracker les votes des différents joueurs
        :param html: HTML de la page d'administration du sondage
        :return: Token de controle du sondage
        """
        soup = BeautifulSoup(html, 'html.parser')
        hidden_input = soup.find("input", {"name": "control"})
        control_token = hidden_input["value"] if hidden_input else None
        return control_token

    @staticmethod
    async def add_player(admin_url, player_name, control_token, choices):
        """
        Ajoute un joueur au sondage afin de permettre de tracker les votes des différents joueurs
        :param admin_url: URL de l'administration du sondage
        :param player_name: Nom du joueur
        :param control_token: Token de controle du sondage
        :param choices: Choix du joueur
        :return:
        """
        data = {
            'control': control_token,
            'name': player_name,
            'save': '',
        }
        data.update(choices)

        async with aiohttp.ClientSession() as session:
            await session.post(admin_url, data=data)

    async def analyze_csv(self, admin_url, players_count):
        """
        Analyse le fichier CSV du sondage et retourne les votants n'ayant pas répondu, la date choisie et
        si tout le monde a répondu.
        Selectionne la date avec le plus de votes positifs, et si plusieurs dates ont le même nombre de votes positifs,
        selectionne la date avec le moins de votes "si nécessaire".
        :param admin_url: URL de l'administration du sondage
        :param players_count: Nombre de joueurs
        :return: Dictionnaire contenant les votants n'ayant pas répondu, la date choisie et si tout le monde a répondu
        """
        poll_id = admin_url.split('/')[-2]
        csv_url = f"{self.BASE_URL}/exportcsv.php?admin={poll_id}"

        async with aiohttp.ClientSession() as session:
            async with session.get(csv_url) as response:
                if response.status == 200:
                    csv_text = await response.text()
                    f = StringIO(csv_text)
                    reader = csv.reader(f, delimiter=',')

                    date_headers = next(reader)[1:]
                    time_headers = next(reader)[1:]

                    slots = list(zip(date_headers, time_headers))
                    slot_yes_responses = {slot: 0 for slot in slots}
                    slot_if_needed_responses = {slot: 0 for slot in slots}
                    non_responders = []

                    for row in reader:
                        attendee_name = row[0]
                        responses = row[1:]
                        is_non_responder = True

                        for i, resp in enumerate(responses, start=1):
                            formatted_response = resp.strip().lower()
                            if formatted_response == 'oui':
                                slot_yes_responses[slots[i - 1]] += 1
                                is_non_responder = False
                            elif formatted_response == 'si nécessaire':
                                slot_if_needed_responses[slots[i - 1]] += 1
                                is_non_responder = False

                        if is_non_responder:
                            non_responders.append(attendee_name)

                    f.close()

                    date_found = None
                    max_yes_responses = 0
                    for slot, yes_count in slot_yes_responses.items():
                        if_needed_count = slot_if_needed_responses[slot]
                        total_count = yes_count + if_needed_count

                        if yes_count > max_yes_responses and total_count == players_count:
                            date_found = f"{slot[0]} {slot[1]}"
                            max_yes_responses = yes_count

                    all_responded = not non_responders

                    return {
                        "non_responders": non_responders,
                        "date_found": date_found,
                        "all_responded": all_responded
                    }

    def initiate_poll(self, poll_author, poll_type='date', lang='fr', title='', description='', email=''):
        """
        Initialise un sondage Framadate et retourne la page de creation. Premiere etape de la creation du sondage
        :param poll_author: Auteur du sondage
        :param poll_type: Type de sondage (date, classic)
        :param lang: Langue du sondage (fr, en, es, de, it, pt, ru, zh)
        :param title: Titre du sondage
        :param description: Description du sondage
        :param email: Email de l'auteur
        :return:
        """
        params = {'type': poll_type, 'lang': lang}
        data = {
            'name': poll_author,
            'mail': email,
            'title': title,
            'description': description,
            'ValueMax': '',
            'customized_url': '',
            'password': '',
            'password_repeat': '',
            'editable': '1',
            'type': poll_type,
            'gotostep2': poll_type,
        }
        response = self.session.post(self.BASE_URL + self.CREATION_ENDPOINT, params=params, data=data)
        self._check_response(response)

    def set_poll_dates(self, date_entries):
        """
        Ajoute les dates au sondage. Seconde etape de la creation du sondage
        :param date_entries: Liste des dates au format dd/mm/yyyy
        :return:
        """
        response = self.session.post(self.BASE_URL + self.DATE_POLL_ENDPOINT, data=date_entries)
        self._check_response(response)

    def confirm_poll(self, end_date):
        """
        Confirme le sondage et retourne la page d'administration. Troisieme etape de la creation du sondage
        :param end_date: Date de fin du sondage au format dd/mm/yyyy
        :return: Page d'administration du sondage
        """
        data = {'enddate': end_date, 'confirmation': 'confirmation'}
        response = self.session.post(self.BASE_URL + self.DATE_POLL_ENDPOINT, data=data)
        self._check_response(response)
        return response

    def create_date_poll(self, poll_author, title, description, email, start_date, num_days, end_date):
        """
        Cree un sondage Framadate et retourne les informations necessaires pour l'administrer
        :param poll_author: Auteur du sondage
        :param title: Titre du sondage
        :param description: Description du sondage
        :param email: Email de l'auteur
        :param start_date: Date de debut du sondage au format dd/mm/yyyy
        :param num_days: Nombre de jours a planifier
        :param end_date: Date de fin du sondage au format dd/mm/yyyy
        :return: Dictionnaire contenant les informations du sondage
        """
        self.initiate_poll(poll_author=poll_author, title=title, description=description, email=email)
        date_entries = self.generate_schedule(start_date, num_days)
        self.set_poll_dates(date_entries)
        response = self.confirm_poll(end_date)
        admin_url = response.url
        html = response.content
        public_url = self.get_public_poll_link(html)
        control_token = self.get_control_token(html)
        choices_count = len(date_entries) - 1 - num_days
        return {
            'admin_url': admin_url,
            'public_url': public_url,
            'choices_count': choices_count,
            'control_token': control_token,
            'expire_at': end_date
        }

