from discord.ext import commands


class VoiceConnectionError(commands.CommandError):
    """Custom Exception class for connection errors."""


class InvalidVoiceChannel(VoiceConnectionError):
    """Exception for cases of invalid Voice Channels."""


class BingImageLanguageError(Exception):
    """Exception returned if the language used for the Bing image creator is not English"""
    # def __init__(self, exception: Exception):
    #     self.message = f'{type(exception)}: {str(exception)}.'
    #     super().__init__(self.message)


class BingImageCookieError(Exception):
    """Exception returned if the cookie seems wrong"""


class BingImageNoResult(Exception):
    """Exception raised if no image was found"""


class BingImageResponseParsingError(Exception):
    """Exception raised if all the four images could not be found in the response of Bing image creator"""
    def __init__(self, cause):
        self.message = cause
        super().__init__(self.message)


class BingImageHTTPError(Exception):
    """Exception to handle connexion problems to bing image creator"""
    def __init__(self, status_code):
        self.message = f"Unexpected status code: {status_code}"
        super().__init__(self.message)


class BingChatResponseParsingError(Exception):
    """Exception to handle all exceptions that can occur during the parsing of bing chat responses"""
    def __init__(self, exception: Exception):
        self.message = f'{type(exception)}: {str(exception)}.'
        super().__init__(self.message)
