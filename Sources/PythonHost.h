#import <Foundation/Foundation.h>

NS_ASSUME_NONNULL_BEGIN
typedef NSString * _Nonnull (^IOWONativeDispatch)(NSString *method, NSString *payload);
NSString * _Nullable IOWOStartEngine(NSString *resourcePath, NSString *dataPath,
                                    IOWONativeDispatch handler, NSError * _Nullable * _Nullable error) NS_SWIFT_NOTHROW;
NSString * _Nullable IOWOEngineSyncStatus(NSError * _Nullable * _Nullable error) NS_SWIFT_NOTHROW;
NS_ASSUME_NONNULL_END
