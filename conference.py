#!/usr/bin/env python

"""
conference.py -- Udacity conference server-side Python App Engine API;
    uses Google Cloud Endpoints

$Id: conference.py,v 1.25 2014/05/24 23:42:19 wesc Exp wesc $

created by wesc on 2014 apr 21

"""

__author__ = 'wesc+api@google.com (Wesley Chun)'


from datetime import datetime
from datetime import date
from datetime import timedelta

import endpoints
from protorpc import messages
from protorpc import message_types
from protorpc import remote

from google.appengine.api import memcache
from google.appengine.api import taskqueue
from google.appengine.ext import ndb

from models import ConflictException
from models import Profile
from models import ProfileMiniForm
from models import ProfileForm
from models import StringMessage
from models import BooleanMessage
from models import Conference
from models import ConferenceForm
from models import ConferenceForms
from models import ConferenceQueryForm
from models import ConferenceQueryForms
from models import TeeShirtSize
from models import Session
from models import SessionForm
from models import SessionForms
from models import UserWishlist
from models import UserWishlistForm
from models import FeaturedSpeakerMemcacheEntry
from models import FeaturedSpeakerMemcacheEntryForm
from models import FeaturedSpeakerMemcacheEntryForms
from models import FeaturedSpeakerMemcacheKeys

from settings import WEB_CLIENT_ID
from settings import ANDROID_CLIENT_ID
from settings import IOS_CLIENT_ID
from settings import ANDROID_AUDIENCE

from utils import getUserId

EMAIL_SCOPE = endpoints.EMAIL_SCOPE
API_EXPLORER_CLIENT_ID = endpoints.API_EXPLORER_CLIENT_ID
MEMCACHE_ANNOUNCEMENTS_KEY = "RECENT_ANNOUNCEMENTS"
ANNOUNCEMENT_TPL = ('Last chance to attend! The following conferences '
                    'are nearly sold out: %s')
MEMCACHE_FEATURED_SPEAKER_KEY = "FeaturedSpeaker"
# - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -

CONFERENCE_DEFAULTS = {
    "city": "Default City",
    "maxAttendees": 0,
    "seatsAvailable": 0,
    "topics": [ "Default", "Topic" ],
}

SESSION_DEFAULTS = {
    "name": "Test Session",
    "highlights": [ "good stuff", "better stuff" ],
    "speaker": "Jesse",
    "duration": 0,
    "typeOfSession": "Test Session",
    "startDate": str(date.today()),
    "startTime": str(datetime.now()),
}

OPERATORS = {
            'EQ':   '=',
            'GT':   '>',
            'GTEQ': '>=',
            'LT':   '<',
            'LTEQ': '<=',
            'NE':   '!='
            }

FIELDS =    {
            'CITY': 'city',
            'TOPIC': 'topics',
            'MONTH': 'month',
            'MAX_ATTENDEES': 'maxAttendees',
            }

CONF_GET_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    websafeConferenceKey=messages.StringField(1)
)

CONF_POST_REQUEST = endpoints.ResourceContainer(
    ConferenceForm,
    websafeConferenceKey=messages.StringField(1)
)

CONF_GET_BY_CITY = endpoints.ResourceContainer(
    message_types.VoidMessage,
    conferenceCity=messages.StringField(1)
)

CONF_GET_BY_TOPIC = endpoints.ResourceContainer(
    message_types.VoidMessage,
    conferenceTopic=messages.StringField(2)
)

SESS_POST_REQUEST = endpoints.ResourceContainer(
    SessionForm,
    websafeConferenceKey=messages.StringField(1)
)

SESS_GET_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    websafeSessionKey=messages.StringField(1)
)

SESS_GET_BY_TYPE_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    websafeConferenceKey=messages.StringField(1),
    sessionType=messages.StringField(2)
)

SESS_GET_BY_SPEAKER_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    speaker=messages.StringField(1)
)

SESS_ADD_TO_WISHLIST_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    websafeSessionKey=messages.StringField(1)
)

GET_SESSIONS_BY_NONTYPE_AND_BEFORE_TIME = endpoints.ResourceContainer(
    message_types.VoidMessage,
    sessionType=messages.StringField(1, required=True),
    endTime=messages.StringField(2, required=True)
)

# - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -


@endpoints.api(name='conference', version='v1', audiences=[ANDROID_AUDIENCE],
    allowed_client_ids=[WEB_CLIENT_ID, API_EXPLORER_CLIENT_ID, ANDROID_CLIENT_ID, IOS_CLIENT_ID],
    scopes=[EMAIL_SCOPE])
class ConferenceApi(remote.Service):
    """Conference API v0.1"""

# Helper functions
    def _getLoggedInUser(self):
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        return user;

# - - - Conference objects - - - - - - - - - - - - - - - - -

    def _copyConferenceToForm(self, conf, displayName):
        """Copy relevant fields from Conference to ConferenceForm."""
        cf = ConferenceForm()
        for field in cf.all_fields():
            if hasattr(conf, field.name):
                # convert Date to date string; just copy others
                if field.name.endswith('Date') or field.name.endswith('Time'):
                    setattr(cf, field.name, str(getattr(conf, field.name)))
                else:
                    setattr(cf, field.name, getattr(conf, field.name))
            elif field.name == "websafeKey":
                setattr(cf, field.name, conf.key.urlsafe())
        if displayName:
            setattr(cf, 'organizerDisplayName', displayName)
        cf.check_initialized()
        return cf

    def _copySessionToForm(self, theSession):
        wl = SessionForm()
        
        for field in wl.all_fields():
            if hasattr(theSession, field.name):
                # Same as above; convert date or time to string
                if field.name.endswith('Date') or field.name.endswith('Time'):
                    setattr(wl, field.name, str(getattr(theSession, field.name)))
                else:
                    setattr(wl, field.name, getattr(theSession, field.name))
            elif field.name == "websafeKey":
                setattr(wl, field.name, theSession.key.urlsafe())
        wl.check_initialized()
        return wl


    def _createConferenceObject(self, request):
        """Create or update Conference object, returning ConferenceForm/request."""
        # preload necessary data items
        user = self._getLoggedInUser()
        user_id = getUserId(user)

        if not request.name:
            raise endpoints.BadRequestException("Conference 'name' field required")

        # copy ConferenceForm/ProtoRPC Message into dict
        data = {field.name: getattr(request, field.name) for field in request.all_fields()}
        del data['websafeKey']
        del data['organizerDisplayName']

        # add default values for those missing (both data model & outbound Message)
        for df in CONFERENCE_DEFAULTS:
            if data[df] in (None, []):
                data[df] = CONFERENCE_DEFAULTS[df]
                setattr(request, df, CONFERENCE_DEFAULTS[df])

        # convert dates from strings to Date objects; set month based on start_date
        if data['startDate']:
            data['startDate'] = datetime.strptime(data['startDate'][:10], "%Y-%m-%d").date()
            data['month'] = data['startDate'].month
        else:
            data['month'] = 0
        if data['endDate']:
            data['endDate'] = datetime.strptime(data['endDate'][:10], "%Y-%m-%d").date()

        # set seatsAvailable to be same as maxAttendees on creation
        if data["maxAttendees"] > 0:
            data["seatsAvailable"] = data["maxAttendees"]
        # generate Profile Key based on user ID and Conference
        # ID based on Profile key get Conference key from ID
        p_key = ndb.Key(Profile, user_id)
        c_id = Conference.allocate_ids(size=1, parent=p_key)[0]
        c_key = ndb.Key(Conference, c_id, parent=p_key)
        data['key'] = c_key
        data['organizerUserId'] = request.organizerUserId = user_id

        # create Conference, send email to organizer confirming
        # creation of Conference & return (modified) ConferenceForm
        Conference(**data).put()
        taskqueue.add(params={'email': user.email(),
            'conferenceInfo': repr(request)},
            url='/tasks/send_confirmation_email'
        )
        return request
                
    def _getUserWishlistByProfile(self, profile):
        #Given a profile, get its key and return the sessions on the wishlist 
        
        if not profile:
            raise endpoints.BadRequestException("Invalid profile!")
        
        #Get the wishlist entries and add them to the wishlist to return.
        wishlistEntries = UserWishlist.query(ancestor=profile.key).fetch(limit=None)
        finishedWishlist = SessionForms()
        
        if wishlistEntries:
            for entry in wishlistEntries:
                theSession = ndb.Key(urlsafe=getattr(entry, "wishlistedSessionKey")).get()
                sf = self._copySessionToForm(theSession)
                sf.check_initialized()
                finishedWishlist.items.append(sf)
                
        return finishedWishlist
        
        #TODO
    def _addSessionToWishlist(self, request):
        #Check if the user is logged in
        
        user = self._getLoggedInUser()
        user_id = getUserId(user)
            
        #Check if the theSession exists
        theSession = ndb.Key(urlsafe=request.websafeSessionKey).get()
        
        if not theSession:
            raise endpoints.BadRequestException("Invalid session key")
        
        #Get user profile
        prof = ndb.Key(Profile, user_id).get()
        
        if not prof:
            raise endpoints.BadRequestException("Unable to find user profile")
        
        wishlistEntry = UserWishlist.query(ancestor=prof.key).filter(
            getattr(UserWishlist, "wishlistedSessionKey") == request.websafeSessionKey
        ).get()
        
        print wishlistEntry
        
        #If the desired wishlist entry doesn't already exist, create it.
        if not wishlistEntry:
            wishlistEntry = UserWishlist(parent=prof.key)
            setattr(wishlistEntry, "wishlistedSessionKey", request.websafeSessionKey)
            wishlistEntry.put()
            
        return self._getUserWishlistByProfile(prof)     

        
    #Same as above, but for sessions.
    def _createSessionObject(self, request):
        self._getLoggedInUser()

        if not request.websafeConferenceKey:
            raise endpoints.BadRequestException("Websafe Conference Key field required")
        
        theConference = ndb.Key(urlsafe=request.websafeConferenceKey).get()
        
        if not theConference:
            raise endpoints.BadRequestException("That conference doesn't exist!")
            
        # Same as above, copy SessionForm/ProtoRPC Message into dict
        data = {field.name: getattr(request, field.name) for field in request.all_fields()}
       
        theConferenceWebsafeKey = data['websafeConferenceKey']
       
        del data['websafeKey']
        del data['websafeConferenceKey']
        
        # Defaults for missing values
        for df in SESSION_DEFAULTS:
            if data[df] in (None, []):
                data[df] = SESSION_DEFAULTS[df]
                setattr(request, df, SESSION_DEFAULTS[df])

        # Convert date and time
        try:
            if data['startDate']:
                data['startDate'] = datetime.strptime(data['startDate'][:10], "%Y-%m-%d").date()
        except ValueError:
            data['startDate'] = date.today()
            
        try:
            if data['startTime']:
                data['startTime'] = datetime.strptime(data['startTime'], "%H:%M:%S")
        except ValueError:
            data['startTime'] = datetime.now()

        # Generate keys
        Session(parent=theConference.key, **data).put()
        
        if (data['speaker']):
            # Check speaker name at this conference for the Featured Speaker memcaches
            speakerSessions = Session.query(ancestor=theConference.key).filter(Session.speaker == data['speaker']).fetch(limit=None)
            
            if (speakerSessions) and (len(speakerSessions) > 1):
                #Check if the memcache entry exists for this speaker and this conference.
   
                theEntry = memcache.get(data['speaker'] + "_" + theConferenceWebsafeKey)
                theKeys = memcache.get(MEMCACHE_FEATURED_SPEAKER_KEY)
                
                entryKey = key = (data['speaker'] + "_" + theConferenceWebsafeKey)
                
                if theKeys is None:
                    theKeys = FeaturedSpeakerMemcacheKeys()
                
                if theEntry is None:
                    theEntry = FeaturedSpeakerMemcacheEntry()
                    theEntry.speaker = data['speaker']
                    theEntry.conferenceWebsafeKey = theConferenceWebsafeKey
                    
                theEntry.sessions = []
                
                for speakerSession in speakerSessions:
                    theEntry.sessions.append(speakerSession.name)
                        
                memcache.set(key = entryKey, 
                    value = theEntry)
                    
                if entryKey not in theKeys.items:
                    theKeys.items.append(entryKey)
                    memcache.set(key = MEMCACHE_FEATURED_SPEAKER_KEY, value = theKeys)
                    
                    
        return request

    def _getFeaturedSpeakersFromMemcache(self):
        # inserted via key MEMCACHE_FEATURED_SPEAKER_KEY
        theKeys = memcache.get(key = MEMCACHE_FEATURED_SPEAKER_KEY)
        thingsToReturn = FeaturedSpeakerMemcacheEntryForms()
        thingsToReturn.check_initialized()
        
        if theKeys:
            for speakerKey in theKeys.items:
                entry = memcache.get(key = speakerKey)
                if entry:
                    thingsToReturn.items.append(self._copyFeaturedSpeakerToForm(entry))
                    
        return thingsToReturn
            
    def _copyFeaturedSpeakerToForm(self, Speaker):
        toReturn = FeaturedSpeakerMemcacheEntryForm()
        toReturn.check_initialized()
        toReturn.speaker = Speaker.speaker
        toReturn.conferenceWebsafeKey = Speaker.conferenceWebsafeKey
        toReturn.sessions = Speaker.sessions
        
        return toReturn
        
    @ndb.transactional()
    def _updateConferenceObject(self, request):
        user = self._getLoggedInUser()
        user_id = getUserId(user)

        # copy ConferenceForm/ProtoRPC Message into dict
        data = {field.name: getattr(request, field.name) for field in request.all_fields()}

        # update existing conference
        conf = ndb.Key(urlsafe=request.websafeConferenceKey).get()
        # check that conference exists
        if not conf:
            raise endpoints.NotFoundException(
                'No conference found with key: %s' % request.websafeConferenceKey)

        # check that user is owner
        if user_id != conf.organizerUserId:
            raise endpoints.ForbiddenException(
                'Only the owner can update the conference.')

        # Not getting all the fields, so don't create a new object; just
        # copy relevant fields from ConferenceForm to Conference object
        for field in request.all_fields():
            data = getattr(request, field.name)
            # only copy fields where we get data
            if data not in (None, []):
                # special handling for dates (convert string to Date)
                if field.name in ('startDate', 'endDate'):
                    data = datetime.strptime(data, "%Y-%m-%d").date()
                    if field.name == 'startDate':
                        conf.month = data.month
                # write to Conference object
                setattr(conf, field.name, data)
                
        conf.put()
        prof = ndb.Key(Profile, user_id).get()
        return self._copyConferenceToForm(conf, getattr(prof, 'displayName'))


    @endpoints.method(ConferenceForm, ConferenceForm, path='conference',
            http_method='POST', name='createConference')
    def createConference(self, request):
        """Create new conference."""
        return self._createConferenceObject(request)

    # createSession(SessionForm, websafeConferenceKey)    
    @endpoints.method(SESS_POST_REQUEST, SessionForm, path='session',
            http_method='POST', name='createSession')
    def createSession(self, request):
        """Create new session."""
        return self._copySessionToForm(self._createSessionObject(request))

    @endpoints.method(CONF_POST_REQUEST, ConferenceForm,
            path='conference/{websafeConferenceKey}',
            http_method='PUT', name='updateConference')
    def updateConference(self, request):
        """Update conference w/provided fields & return w/updated info."""
        return self._updateConferenceObject(request)

    @endpoints.method(message_types.VoidMessage, 
        FeaturedSpeakerMemcacheEntryForms,
        http_method="GET", 
        name="getFeaturedSpeaker", 
        path="getFeaturedSpeaker")
    def getFeaturedSpeaker(self, request):
        """Get the featured speaker for each conference."""
        return self._getFeaturedSpeakersFromMemcache()

    @endpoints.method(CONF_GET_REQUEST, ConferenceForm,
            path='conference/{websafeConferenceKey}',
            http_method='GET', name='getConference')
    def getConference(self, request):
        """Return requested conference (by websafeConferenceKey)."""
        # get Conference object from request; bail if not found
        conf = ndb.Key(urlsafe=request.websafeConferenceKey).get()
        if not conf:
            raise endpoints.NotFoundException(
                'No conference found with key: %s' % request.websafeConferenceKey)
        prof = conf.key.parent().get()
        return self._copyConferenceToForm(conf, getattr(prof, 'displayName'))

    #Return sessions by conference.
    @endpoints.method(CONF_GET_REQUEST, 
            SessionForms, path='getConferenceSessions/{websafeConferenceKey}',
            http_method='GET', name='getConferenceSessions')
    def getConferenceSessions(self, request):
        """Get conference sessions by websafe conference key."""
        
        self._getLoggedInUser()
        
        theConference = ndb.Key(urlsafe=request.websafeConferenceKey).get()
        theSessions = Session.query(ancestor=theConference.key)
        
        return SessionForms(
            items=[self._copySessionToForm(oneSession) for oneSession in theSessions]
        )

    @endpoints.method(message_types.VoidMessage, ConferenceForms,
            path='getConferencesCreated',
            http_method='POST', name='getConferencesCreated')
    def getConferencesCreated(self, request):
        """Return conferences created by user."""
        # make sure user is authed
        user = self._getLoggedInUser()
        user_id = getUserId(user)

        # create ancestor query for all key matches for this user
        confs = Conference.query(ancestor=ndb.Key(Profile, user_id))
        prof = ndb.Key(Profile, user_id).get()
        # return set of ConferenceForm objects per Conference
        return ConferenceForms(
            items=[self._copyConferenceToForm(conf, getattr(prof, 'displayName')) for conf in confs]
        )
        
    @endpoints.method(CONF_GET_BY_CITY, ConferenceForms,
        path="getConferencesByCity",
        http_method="POST", name="getConferencesByCity")
    def getConferencesByCity(self, request):
        """Get conferences by city."""
        confs = Conference.query().filter(getattr(Conference, "city") == request.conferenceCity)
        prof = self._getProfileFromUser()
        
        return ConferenceForms(
            items=[self._copyConferenceToForm(conf, getattr(prof, 'displayName')) for conf in confs]
        )
        
    @endpoints.method(CONF_GET_BY_TOPIC, ConferenceForms,
        path="getConferencesByExactTopic",
        http_method="POST", name="getConferencesByExactTopic")
    def getConferencesByExactTopic(self, request):
        """Get conferences by topic.  Must be a complete match; use getConferencesCreated and copy a topic from there."""
        confs = Conference.query(Conference.topics == request.conferenceTopic)
        prof = self._getProfileFromUser()
        
        return ConferenceForms(
            items=[self._copyConferenceToForm(conf, getattr(prof, 'displayName')) for conf in confs]
        )
        
    @endpoints.method(GET_SESSIONS_BY_NONTYPE_AND_BEFORE_TIME, SessionForms,
        path="getSessionsNotOfTypeAndBeforeTime",
        http_method="POST", name="getSessionsNotOfTypeAndBeforeTime")
    def getSessionsNotOfTypeAndBeforeTime(self, request):
        """Get sessions that are NOT a given type, and that finish before the given 24H time."""

        sessions = Session.query(Session.typeOfSession != request.sessionType)
        sessionsToReturn = SessionForms()
        sessionsToReturn.check_initialized()
        
        cutoffTime = datetime.strptime(request.endTime, "%H:%M:%S")
        
        for sess in sessions:
            #For each session that finishes before the cutoff time, add it to the list to return.
            if (cutoffTime > (sess.startTime + timedelta(minutes = sess.duration))):
                sessionsToReturn.items.append(self._copySessionToForm(sess))
        
        return sessionsToReturn

    @endpoints.method(SESS_GET_BY_TYPE_REQUEST, SessionForms,
            path='getConferenceSessionsByType',
            http_method='POST', name='getConferenceSessionsByType')
    def getConferenceSessionsByType(self, request):
        """Get conference sessions by conference and session type."""
        self._getLoggedInUser()
        
        theConference = ndb.Key(urlsafe=request.websafeConferenceKey).get()
        theSessions = Session.query(ancestor=theConference.key).filter(
            getattr(Session, "typeOfSession") == request.sessionType)
        
        return SessionForms(
            items=[self._copySessionToForm(oneSession) for oneSession in theSessions]
        )
                
    @endpoints.method(SESS_GET_BY_SPEAKER_REQUEST, SessionForms,
            path='getConferenceSessionsBySpeaker',
            http_method='POST', name='getConferenceSessionsBySpeaker')
    def getConferenceSessionsBySpeaker(self, request):
        """Get conference sessions by speaker."""
        self._getLoggedInUser()
            
        theSessions = Session.query(getattr(Session, "speaker") == request.speaker)
        
        return SessionForms(
            items=[self._copySessionToForm(oneSession) for oneSession in theSessions]
        )
        
    @endpoints.method(SESS_GET_REQUEST, SessionForms,
        path='addSessionToWishlist', http_method="POST",
        name="addSessionToWishlist")
    def addSessionToWishlist(self, request):
        """Add a session to a user's wishist by session websafe key."""
        return self._addSessionToWishlist(request)
            
    @endpoints.method(message_types.VoidMessage, SessionForms,
            path='getSessionsInWishlist', http_method="GET",
            name="getSessionsInWishlist")
    def getSessionsInWishlist(self, request):
        """Get the sessions on your wishlist."""
        
        # Get user profile, and pass it to _getUserWishlistByProfile()
        user = self._getLoggedInUser()
        user_id = getUserId(user)
        
        prof = ndb.Key(Profile, user_id).get()
        
        return self._getUserWishlistByProfile(prof)
        

    def _getQuery(self, request):
        """Return formatted query from the submitted filters."""
        q = Conference.query()
        inequality_filter, filters = self._formatFilters(request.filters)

        # If exists, sort on inequality filter first
        if not inequality_filter:
            q = q.order(Conference.name)
        else:
            q = q.order(ndb.GenericProperty(inequality_filter))
            q = q.order(Conference.name)

        for filtr in filters:
            if filtr["field"] in ["month", "maxAttendees"]:
                filtr["value"] = int(filtr["value"])
            formatted_query = ndb.query.FilterNode(filtr["field"], filtr["operator"], filtr["value"])
            q = q.filter(formatted_query)
        return q


    def _formatFilters(self, filters):
        """Parse, check validity and format user supplied filters."""
        formatted_filters = []
        inequality_field = None

        for f in filters:
            filtr = {field.name: getattr(f, field.name) for field in f.all_fields()}

            try:
                filtr["field"] = FIELDS[filtr["field"]]
                filtr["operator"] = OPERATORS[filtr["operator"]]
            except KeyError:
                raise endpoints.BadRequestException("Filter contains invalid field or operator.")

            # Every operation except "=" is an inequality
            if filtr["operator"] != "=":
                # check if inequality operation has been used in previous filters
                # disallow the filter if inequality was performed on a different field before
                # track the field on which the inequality operation is performed
                if inequality_field and inequality_field != filtr["field"]:
                    raise endpoints.BadRequestException("Inequality filter is allowed on only one field.")
                else:
                    inequality_field = filtr["field"]

            formatted_filters.append(filtr)
        return (inequality_field, formatted_filters)


    @endpoints.method(ConferenceQueryForms, ConferenceForms,
            path='queryConferences',
            http_method='POST',
            name='queryConferences')
    def queryConferences(self, request):
        """Query for conferences."""
        conferences = self._getQuery(request)

        # need to fetch organiser displayName from profiles
        # get all keys and use get_multi for speed
        organisers = [(ndb.Key(Profile, conf.organizerUserId)) for conf in conferences]
        profiles = ndb.get_multi(organisers)

        # put display names in a dict for easier fetching
        names = {}
        for profile in profiles:
            names[profile.key.id()] = profile.displayName

        # return individual ConferenceForm object per Conference
        return ConferenceForms(
                items=[self._copyConferenceToForm(conf, names[conf.organizerUserId]) for conf in \
                conferences]
        )


# - - - Profile objects - - - - - - - - - - - - - - - - - - -

    def _copyProfileToForm(self, prof):
        """Copy relevant fields from Profile to ProfileForm."""
        # copy relevant fields from Profile to ProfileForm
        pf = ProfileForm()
        for field in pf.all_fields():
            if hasattr(prof, field.name):
                # convert t-shirt string to Enum; just copy others
                if field.name == 'teeShirtSize':
                    setattr(pf, field.name, getattr(TeeShirtSize, getattr(prof, field.name)))
                else:
                    setattr(pf, field.name, getattr(prof, field.name))
        pf.check_initialized()
        return pf


    def _getProfileFromUser(self):
        """Return user Profile from datastore, creating new one if non-existent."""
        # make sure user is authed
        user = self._getLoggedInUser()

        # get Profile from datastore
        user_id = getUserId(user)
        p_key = ndb.Key(Profile, user_id)
        profile = p_key.get()
        # create new Profile if not there
        if not profile:
            profile = Profile(
                key = p_key,
                displayName = user.nickname(),
                mainEmail= user.email(),
                teeShirtSize = str(TeeShirtSize.NOT_SPECIFIED),
            )
            profile.put()

        return profile 


    def _doProfile(self, save_request=None):
        """Get user Profile and return to user, possibly updating it first."""
        # get user Profile
        prof = self._getProfileFromUser()

        # if saveProfile(), process user-modifyable fields
        if save_request:
            for field in ('displayName', 'teeShirtSize'):
                if hasattr(save_request, field):
                    val = getattr(save_request, field)
                    if val:
                        setattr(prof, field, str(val))
                        prof.put()

        return self._copyProfileToForm(prof)


    @endpoints.method(message_types.VoidMessage, ProfileForm,
            path='profile', http_method='GET', name='getProfile')
    def getProfile(self, request):
        """Return user profile."""
        return self._doProfile()


    @endpoints.method(ProfileMiniForm, ProfileForm,
            path='profile', http_method='POST', name='saveProfile')
    def saveProfile(self, request):
        """Update & return user profile."""
        return self._doProfile(request)


# - - - Announcements - - - - - - - - - - - - - - - - - - - -

    @staticmethod
    def _cacheAnnouncement():
        """Create Announcement & assign to memcache; used by
        memcache cron job & putAnnouncement().
        """
        confs = Conference.query(ndb.AND(
            Conference.seatsAvailable <= 5,
            Conference.seatsAvailable > 0)
        ).fetch(projection=[Conference.name])

        if confs:
            # If there are almost sold out conferences,
            # format announcement and set it in memcache
            announcement = ANNOUNCEMENT_TPL % (
                ', '.join(conf.name for conf in confs))
            memcache.set(MEMCACHE_ANNOUNCEMENTS_KEY, announcement)
        else:
            # If there are no sold out conferences,
            # delete the memcache announcements entry
            announcement = ""
            memcache.delete(MEMCACHE_ANNOUNCEMENTS_KEY)

        return announcement


    @endpoints.method(message_types.VoidMessage, StringMessage,
            path='conference/announcement/get',
            http_method='GET', name='getAnnouncement')
    def getAnnouncement(self, request):
        """Return Announcement from memcache."""
        return StringMessage(data=memcache.get(MEMCACHE_ANNOUNCEMENTS_KEY) or "")


# - - - Registration - - - - - - - - - - - - - - - - - - - -

    @ndb.transactional(xg=True)
    def _conferenceRegistration(self, request, reg=True):
        """Register or unregister user for selected conference."""
        retval = None
        prof = self._getProfileFromUser() # get user Profile

        # check if conf exists given websafeConfKey
        # get conference; check that it exists
        wsck = request.websafeConferenceKey
        conf = ndb.Key(urlsafe=wsck).get()
        if not conf:
            raise endpoints.NotFoundException(
                'No conference found with key: %s' % wsck)

        # register
        if reg:
            # check if user already registered otherwise add
            if wsck in prof.conferenceKeysToAttend:
                raise ConflictException(
                    "You have already registered for this conference")

            # check if seats avail
            if conf.seatsAvailable <= 0:
                raise ConflictException(
                    "There are no seats available.")

            # register user, take away one seat
            prof.conferenceKeysToAttend.append(wsck)
            conf.seatsAvailable -= 1
            retval = True

        # unregister
        else:
            # check if user already registered
            if wsck in prof.conferenceKeysToAttend:

                # unregister user, add back one seat
                prof.conferenceKeysToAttend.remove(wsck)
                conf.seatsAvailable += 1
                retval = True
            else:
                retval = False

        # write things back to the datastore & return
        prof.put()
        conf.put()
        return BooleanMessage(data=retval)


    @endpoints.method(message_types.VoidMessage, ConferenceForms,
            path='conferences/attending',
            http_method='GET', name='getConferencesToAttend')
    def getConferencesToAttend(self, request):
        """Get list of conferences that user has registered for."""
        prof = self._getProfileFromUser() # get user Profile
        conf_keys = [ndb.Key(urlsafe=wsck) for wsck in prof.conferenceKeysToAttend]
        conferences = ndb.get_multi(conf_keys)

        # get organizers
        organisers = [ndb.Key(Profile, conf.organizerUserId) for conf in conferences]
        profiles = ndb.get_multi(organisers)

        # put display names in a dict for easier fetching
        names = {}
        for profile in profiles:
            names[profile.key.id()] = profile.displayName

        # return set of ConferenceForm objects per Conference
        return ConferenceForms(items=[self._copyConferenceToForm(conf, names[conf.organizerUserId])\
         for conf in conferences]
        )


    @endpoints.method(CONF_GET_REQUEST, BooleanMessage,
            path='conference/{websafeConferenceKey}',
            http_method='POST', name='registerForConference')
    def registerForConference(self, request):
        """Register user for selected conference."""
        return self._conferenceRegistration(request)


    @endpoints.method(CONF_GET_REQUEST, BooleanMessage,
            path='conference/{websafeConferenceKey}',
            http_method='DELETE', name='unregisterFromConference')
    def unregisterFromConference(self, request):
        """Unregister user for selected conference."""
        return self._conferenceRegistration(request, reg=False)


    @endpoints.method(message_types.VoidMessage, ConferenceForms,
            path='filterPlayground',
            http_method='GET', name='filterPlayground')
    def filterPlayground(self, request):
        """Filter Playground"""
        q = Conference.query()
        q = q.filter(Conference.city=="London")
        q = q.filter(Conference.topics=="Medical Innovations")
        q = q.filter(Conference.month==6)

        return ConferenceForms(
            items=[self._copyConferenceToForm(conf, "") for conf in q]
        )
        


api = endpoints.api_server([ConferenceApi]) # register API
