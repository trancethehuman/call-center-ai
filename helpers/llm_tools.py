from azure.communication.callautomation import CallAutomationClient
from helpers.call_utils import ContextEnum as CallContextEnum, handle_play
from helpers.config import CONFIG
from helpers.llm_utils import function_schema
from helpers.logging import build_logger
from inspect import getmembers, isfunction
from models.call import CallStateModel
from models.message import (
    ActionEnum as MessageActionEnum,
    MessageModel,
    PersonaEnum as MessagePersonaEnum,
    StyleEnum as MessageStyleEnum,
)
from models.reminder import ReminderModel
from openai.types.chat import ChatCompletionToolParam
from pydantic import ValidationError
from typing import Awaitable, Callable, Annotated, Literal, TypedDict
import asyncio


_logger = build_logger(__name__)
_search = CONFIG.ai_search.instance()
_sms = CONFIG.sms.instance()


class UpdateClaimDict(TypedDict):
    field: str
    value: str


class LlmPlugins:
    call: CallStateModel
    cancellation_callback: Callable[[], Awaitable]
    client: CallAutomationClient
    post_call_intelligence: Callable[[CallStateModel], None]
    style: MessageStyleEnum = MessageStyleEnum.NONE
    user_callback: Callable[[str, MessageStyleEnum], Awaitable]

    def __init__(
        self,
        call: CallStateModel,
        cancellation_callback: Callable[[], Awaitable],
        client: CallAutomationClient,
        post_call_intelligence: Callable[[CallStateModel], None],
        user_callback: Callable[[str, MessageStyleEnum], Awaitable],
    ):
        self.call = call
        self.cancellation_callback = cancellation_callback
        self.client = client
        self.post_call_intelligence = post_call_intelligence
        self.user_callback = user_callback

    async def end_call(self) -> str:
        """
        Use this if the customer said they want to end the call.

        # Behavior
        1. Hangup the call for everyone
        2. The call with Assistant is ended

        # Rules
        - Requires an explicit verbal validation from the customer
        - Never use this action directly after a recall

        # Examples
        - 'Goodbye, see you tomorrow'
        - 'I want to hangup'
        """
        await self.cancellation_callback()
        await handle_play(
            call=self.call,
            client=self.client,
            context=CallContextEnum.GOODBYE,
            text=await CONFIG.prompts.tts.goodbye(self.call),
        )
        return "Call ended"

    async def new_claim(
        self,
        customer_response: Annotated[
            str,
            "Phrase used to confirm the creation of a new claim. This phrase will be spoken to the user. Describe what you're doing in one sentence. Example: 'I am creating a new claim for a car accident.', 'A new claim for a stolen watch is being created.'.",
        ],
    ) -> str:
        """
        Use this if the customer wants to create a new claim.

        # Behavior
        1. Old claim is stored but not accessible anymore
        2. Reset the Assistant claim

        # Rules
        - Approval from the customer must be explicitely given (e.g. 'I want to create a new claim')
        - This should be used only when the subject is totally different
        """
        await self.user_callback(customer_response, self.style)
        # Launch post-call intelligence for the current call
        self.post_call_intelligence(self.call)
        # Store the last message and use it at first message of the new claim
        last_message = self.call.messages[-1]
        call = CallStateModel(
            initiate=self.call.initiate.model_copy(),
            voice_id=self.call.voice_id,
        )
        call.messages += [
            MessageModel(
                action=MessageActionEnum.CALL,
                content="",
                persona=MessagePersonaEnum.HUMAN,
            ),
            last_message,
        ]
        self.call = call
        return "Claim, reminders and messages reset"

    async def new_or_updated_reminder(
        self,
        customer_response: Annotated[
            str,
            "Phrase used to confirm the update, in the same language as the customer. This phrase will be spoken to the user. Describe what you're doing in one sentence. Example: 'I am creating a reminder for next week to call you back.', 'A todo for next week is planned.'.",
        ],
        description: Annotated[
            str,
            "Description of the reminder, in English. Should be detailed enough to be understood by anyone. Example: 'Call back customer to get more details about the accident', 'Send analysis report to the customer'.",
        ],
        due_date_time: Annotated[
            str,
            "Datetime when the reminder should be triggered. Should be in the future, in the ISO format.",
        ],
        owner: Annotated[
            str,
            "The owner of the reminder, in English. Can be 'customer', 'assistant', or a third party from the claim. Try to be as specific as possible, with a name. Example: 'customer', 'assistant', 'contact', 'witness', 'police'.",
        ],
        title: Annotated[
            str,
            "Short title of the reminder, in English. Should be short and concise, in the format 'Verb + Subject'. Title is unique and allows the reminder to be updated. Example: 'Call back customer', 'Send analysis report', 'Study replacement estimates for the stolen watch'.",
        ],
    ) -> str:
        """
        Use this if you think there is something important to do.

        # Behavior
        1. Create a reminder with the given values
        2. Return a confirmation message

        # Rules
        - A reminder should be as specific as possible
        - If a reminder already exists, it will be updated with the new values
        - The due date should be in the future
        """
        await self.user_callback(customer_response, self.style)

        # Check if reminder already exists, if so update it
        for reminder in self.call.reminders:
            if reminder.title == title:
                try:
                    reminder.description = description
                    reminder.due_date_time = due_date_time  # type: ignore
                    reminder.owner = owner
                    return f'Reminder "{title}" updated.'
                except ValidationError as e:
                    return f'Failed to edit reminder "{title}": {e.json()}'

        # Create new reminder
        try:
            reminder = ReminderModel(
                description=description,
                due_date_time=due_date_time,  # type: ignore
                owner=owner,
                title=title,
            )
            self.call.reminders.append(reminder)
            return f'Reminder "{title}" created.'
        except ValidationError as e:
            return f'Failed to create reminder "{title}": {e.json()}'

    async def updated_claim(
        self,
        customer_response: Annotated[
            str,
            "Phrase used to confirm the update, in the same language as the customer. This phrase will be spoken to the user. Describe what you're doing in one sentence. Example: 'I am updating the your name to Marie-Jeanne Duchemin and your email to mariejeanne@gmail.com.'.",
        ],
        updates: Annotated[
            list[UpdateClaimDict],
            """
            The field to update, in English.

            # Available fields
            {% for name, info in call.initiate.claim_model().model_fields.items() %}
            {% if not info.description %}
            - {{ name }}
            {% else %}
            - '{{ name }}', {{ info.description }}
            {% endif %}
            {% endfor %}

            # Data format
            - For dates, use YYYY-MM-DD HH:MM format (e.g. 2024-02-01 18:58)
            - For phone numbers, use E164 format (e.g. +33612345678)

            # Response format
            [{'field': '[field]', 'value': '[value]'}]

            # Examples
            - [{'field': 'policyholder_email', 'value': 'mariejeanne@gmail.com'}]
            - [{'field': 'policyholder_name', 'value': 'Marie-Jeanne Duchemin'}, {'field': 'policyholder_phone', 'value': '+33612345678'}]
            """,
        ],
    ) -> str:
        """
        Use this if the customer wants to update one or more fields in the claim.

        # Behavior
        1. Update the claim with the new values
        2. Return a confirmation message

        # Rules
        - For values, it is OK to approximate dates if the customer is not precise (e.g., "last night" -> today 04h, "I'm stuck on the highway" -> now)
        - It is best to update multiple fields at once
        """
        await self.user_callback(customer_response, self.style)
        # Update all claim fields
        res = "# Updated fields"
        for field in updates:
            res += f"\n- {self._update_claim_field(field)}"
        return res

    def _update_claim_field(self, update: UpdateClaimDict) -> str:
        field = update["field"]
        new_value = update["value"]
        try:
            old_value = self.call.claim.get(field, None)
            self.call.claim[field] = new_value
            CallStateModel.model_validate(self.call)  # Force a re-validation
            return f'Updated claim field "{field}" with value "{new_value}".'
        except ValidationError as e:  # Catch error to inform LLM and rollback changes
            self.call.claim[field] = old_value
            return f'Failed to edit field "{field}": {e.json()}'

    async def talk_to_human(self) -> str:
        """
        Use this if the customer wants to talk to a human and Assistant is unable to help.

        # Behavior
        1. Transfer the customer to an human agent
        2. The call with Assistant is ended

        # Rules
        - Requires an explicit verbal validation from the customer
        - Never use this action directly after a recall

        # Examples
        - 'I want to talk to a human'
        - 'I want to talk to a real person'
        """
        await self.cancellation_callback()
        await handle_play(
            call=self.call,
            client=self.client,
            context=CallContextEnum.CONNECT_AGENT,
            text=await CONFIG.prompts.tts.end_call_to_connect_agent(self.call),
        )
        return "Transferring to human agent"

    async def search_document(
        self,
        customer_response: Annotated[
            str,
            "Phrase used to confirm the search, in the same language as the customer. This phrase will be spoken to the user. Describe what you're doing in one sentence. Example: 'I am searching for the document about the car accident.', 'I am looking for the contract details.'.",
        ],
        queries: Annotated[
            list[str],
            "The text queries to perform the search, in English. Example: ['How much does it cost to repair a broken window?', 'What are the requirements to ask for a cyber attack insurance?']",
        ],
    ) -> str:
        """
        Use this if the customer wants to search for a public specific information you don't have.

        # Rules
        - Multiple queries should be used with different viewpoints and vocabulary
        - The search should be as specific as possible

        # Searchable topics
        contract, law, regulation, article, procedure, guide
        """
        await self.user_callback(customer_response, self.style)
        # Execute in parallel
        tasks = await asyncio.gather(
            *[
                _search.training_asearch_all(text=query, lang="en-US")
                for query in queries
            ]
        )
        # Flatten, remove duplicates, and sort by score
        trainings = sorted(set(training for task in tasks for training in task or []))
        # Format results
        res = "# Search results"
        for training in trainings:
            res += f"\n- {training.title}: {training.content}"
        return res

    async def notify_emergencies(
        self,
        customer_response: Annotated[
            str,
            "Phrase used to confirm the action. This phrase will be spoken to the user. Describe what you're doing in one sentence. Example: 'I am notifying the emergency services.', 'Police number is confirmed.'.",
        ],
        reason: Annotated[
            str,
            "The reason to notify the emergency services. Should be detailed enough to be understood by anyone. Example: 'A person is having a heart attack', 'A child is being attacked by a dog'.",
        ],
        location: Annotated[
            str,
            "The location of the emergency. Should be detailed enough to be understood by anyone. Should contains details like the floor, the building, the code to enter, etc. Example: '123 rue de la paix 75000 Paris, Building A, 3rd floor, code 1234', '12 avenue de la manivelle 13000 Marseille, behind the red door'.",
        ],
        contact: Annotated[
            str,
            "The local contact of a person on site. Should be detailed enough to be understood by anyone. Should contains details like the name, the phone number, etc. Example: 'Marie-Jeanne, +33735119775', 'Jean-Pierre, wear a red hat'.",
        ],
        service: Annotated[
            Literal["police", "firefighters", "pharmacy", "veterinarian", "hospital"],
            "The emergency service to notify.",
        ],
    ) -> str:
        """
        Use this if the customer wants to notify the emergency services for a specific reason.

        # Behavior
        1. A record is stored in the system
        2. A notification is sent to the emergency services

        # Rules
        - Use it only if the situation is critical and requires immediate intervention

        # Examples
        - 'A child is lying on the ground and is not moving'
        - 'I am stuck in a car in fire'
        - 'My neighbor is having a heart attack'
        """
        await self.user_callback(customer_response, self.style)
        # TODO: Implement notification to emergency services for production usage
        _logger.info(
            f"Notifying {service}, location {location}, contact {contact}, reason {reason}."
        )
        return f"Notifying {service} for {reason}."

    async def send_sms(
        self,
        customer_response: Annotated[
            str,
            "Phrase used to confirm the action. This phrase will be spoken to the user. Describe what you're doing in one sentence. Example: 'I am sending a SMS to your phone number.', 'SMS with the details is sent.'.",
        ],
        message: Annotated[
            str,
            "The message to send to the customer.",
        ],
    ) -> str:
        """
        Use when there is a real need to send a SMS to the customer.

        # Usage examples
        - Ask a question, if the call quality is bad
        - Confirm a detail like a reference number, if there is a misunderstanding
        - Send a confirmation, if the customer wants to have a written proof
        """
        await self.user_callback(customer_response, self.style)
        success = await _sms.asend(
            content=message,
            phone_number=self.call.initiate.phone_number,
        )
        if not success:
            return "Failed to send SMS"
        self.call.messages.append(
            MessageModel(
                action=MessageActionEnum.SMS,
                content=message,
                persona=MessagePersonaEnum.ASSISTANT,
            )
        )
        return "SMS sent"

    @staticmethod
    async def to_openai(call: CallStateModel) -> list[ChatCompletionToolParam]:
        return await asyncio.gather(
            *[
                function_schema(type, call=call)
                for name, type in getmembers(LlmPlugins, isfunction)
                if not name.startswith("_") and name != "to_openai"
            ]
        )
