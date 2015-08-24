App Engine application for the Udacity training course.

## Products
- [App Engine][1]

## Language
- [Python][2]

## APIs
- [Google Cloud Endpoints][3]

## Setup Instructions
1. Update the value of `application` in `app.yaml` to the app ID you
   have registered in the App Engine admin console and would like to use to host
   your instance of this sample.
1. Update the values at the top of `settings.py` to
   reflect the respective client IDs you have registered in the
   [Developer Console][4].
1. Update the value of CLIENT_ID in `static/js/app.js` to the Web client ID
1. (Optional) Mark the configuration files as unchanged as follows:
   `$ git update-index --assume-unchanged app.yaml settings.py static/js/app.js`
1. Run the app with the devserver using `dev_appserver.py DIR`, and ensure it's running by visiting your local server's address (by default [localhost:8080][5].)
1. (Optional) Generate your client library(ies) with [the endpoints tool][6].
1. Deploy your application.


[1]: https://developers.google.com/appengine
[2]: http://python.org
[3]: https://developers.google.com/appengine/docs/python/endpoints/
[4]: https://console.developers.google.com/
[5]: https://localhost:8080/
[6]: https://developers.google.com/appengine/docs/python/endpoints/endpoints_tool


API explorer link:
https://apis-explorer.appspot.com/apis-explorer/?base=https://preveyj-fswdnd-project4.appspot.com/_ah/api#s/conference/v1/

AppEngine App ID:
preveyj-fswdnd-project4


Developer explanations:

Task 1:

I implemented Session as a full class according to the requirements, and as a 
'child' of Conference.  A session name is required, but nothing else is.  All 
properties except Duration, Start Date, and Start Time are string properties.  
Duration is an integer, Start Date is a plain date, and Start Time is a 
DateTime.  I chose to implement Speaker as a plain string because nothing else 
in the requirement documents ascribe any other properties to speaker besides 
their name.  Other than that, where possible I made design decisions based on 
expediency.


Task 3:

I implemented getConferencesByCity() and getConferencesByExactTopic(), to 
provide additional search options for conferences.  getConferencesByCity() 
can be used to find conferences taking place in a particular city, and 
getConferencesByExactTopic() can be used to find conferences covering a 
particular topic.


Task 4:

For the "non-workshop sessions before 7PM" problem, we could implement a 
specific query to handle that: find sessions excluding the given type, and
begin before the given time.  The problem is that this is a highly specific
query that doesn't have a lot of reusability outside of this problem, and that
only session start times and durations are stored; what if the user wants 
sessions that don't go past 7 PM instead of just starting before then?  
Solving the time problem should be relatively easy; just take the given time, 
and run a search for sessions whose starting time plus duration in minutes are 
less than the given time cutoff: 
    ((StartTime + durationInMinutes) <= CutoffTime).

The solution to the specificity problem might be to just provide an enpdoint 
that takes whatever query criteria the client provides, validates it, and 
returns the results.  Something like Microsoft's OData could be ideal too, but 
I don't know enough about Python or the Google endpoint API to identify 
anything similar to the IQueryable object that Microsoft uses to provide that 
functionality.  There are two libraries for Python listed at 
http://www.odata.org/libraries/ , but I haven't looked at either one to see 
how they'd interact with the Google endpoints API.