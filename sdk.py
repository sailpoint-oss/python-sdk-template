from sailpoint import (
    ApiClient,
    AccountsApi,
    TransformsApi,
    SearchApi,
)
from sailpoint.configuration import Configuration
from sailpoint.paginator import Paginator
from sailpoint.search.models.search import Search
from pprint import pprint

configuration = Configuration()

with ApiClient(configuration) as api_client:
    # List transforms
    api_instance = TransformsApi(api_client)
    try:
        api_response = api_instance.list_transforms_v1()
        print("The response of TransformsApi->list_transforms_v1:\n")
        for transform in api_response:
            pprint(transform.name)
    except Exception as e:
        print("Exception when calling TransformsApi->list_transforms_v1: %s\n" % e)

    # List accounts
    api_instance = AccountsApi(api_client)
    try:
        api_response = api_instance.list_accounts_v1()
        print("The response of AccountsApi->list_accounts_v1:\n")
        for account in api_response:
            pprint(account.name)
    except Exception as e:
        print("Exception when calling AccountsApi->list_accounts_v1: %s\n" % e)

    # Use the paginator with search
    search = Search()
    search.indices = ['identities']
    search.query = {'query': '*'}
    search.sort = ['-name']

    identities = Paginator.paginate_search(SearchApi(api_client), search, 250, 1000)
    for identity in identities:
        print(identity['name'])

    # Use the paginator to paginate 1000 accounts 100 at a time
    accounts = Paginator.paginate(
        AccountsApi(api_client).list_accounts_v1_with_http_info, 1000, limit=100
    )
    print(len(accounts.data))
    for account in accounts.data:
        print(account.name)
